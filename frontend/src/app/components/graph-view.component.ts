import {
  Component, OnInit, OnDestroy, ElementRef, ViewChild,
  AfterViewInit, signal, HostListener,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import * as d3 from 'd3';
import { ApiService, GraphData, GraphNode, GraphEdge, EntityDetail } from '../services/api.service';
import { Subscription, interval } from 'rxjs';
import { switchMap } from 'rxjs/operators';

interface D3Node extends d3.SimulationNodeDatum, GraphNode {
  x: number; y: number; vx: number; vy: number;
}
interface D3Link extends d3.SimulationLinkDatum<D3Node> {
  source: D3Node; target: D3Node;
  relation: string; weight: number;
}

const TYPE_COLOR: Record<string, string> = {
  person:       '#60a5fa',
  organization: '#34d399',
  location:     '#fbbf24',
  concept:      '#a78bfa',
  event:        '#f87171',
  date:         '#fb923c',
  product:      '#22d3ee',
  law:          '#e879f9',
  disease:      '#f43f5e',
  drug:         '#84cc16',
  other:        '#94a3b8',
};

function typeColor(t: string) { return TYPE_COLOR[t.toLowerCase()] ?? '#94a3b8'; }
function nodeRadius(deg: number) { return Math.max(8, Math.min(22, 8 + Math.sqrt(deg) * 2.5)); }

@Component({
  selector: 'app-graph-view',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="gv-shell">
      <!-- toolbar -->
      <div class="toolbar">
        <div class="search-wrap">
          <svg class="search-icon" viewBox="0 0 20 20" fill="currentColor">
            <path fill-rule="evenodd" d="M9 3a6 6 0 100 12A6 6 0 009 3zm-8 6a8 8 0 1114.32 4.906l4.387 4.387a1 1 0 01-1.414 1.414l-4.387-4.387A8 8 0 011 9z" clip-rule="evenodd"/>
          </svg>
          <input class="search-input" [(ngModel)]="searchTerm" (ngModelChange)="onSearch($event)"
                 placeholder="Search entities…" autocomplete="off"/>
          @if (searchTerm) {
            <button class="search-clear" (click)="searchTerm=''; onSearch('')">✕</button>
          }
        </div>

        <div class="filter-pills">
          @for (type of entityTypes; track type) {
            <button class="filter-pill"
                    [style.border-color]="activeFilters().has(type) ? typeColor(type) : 'transparent'"
                    [style.color]="activeFilters().has(type) ? typeColor(type) : 'var(--text-tertiary)'"
                    [style.background]="activeFilters().has(type) ? hexToRgba(typeColor(type), 0.1) : 'transparent'"
                    (click)="toggleFilter(type)">
              <span class="filter-dot" [style.background]="typeColor(type)"></span>
              {{ type }}
            </button>
          }
        </div>

        <div class="toolbar-right">
          <button class="tb-btn" title="Zoom in"  (click)="zoomBy(1.3)">+</button>
          <button class="tb-btn" title="Zoom out" (click)="zoomBy(0.77)">−</button>
          <button class="tb-btn" title="Fit view"  (click)="fitView()">⊡</button>
          <button class="tb-btn" title="Refresh"   (click)="loadGraph()">↺</button>
        </div>
      </div>

      <!-- canvas area -->
      <div class="canvas-wrap">
        <svg #svgEl class="graph-svg">
          <defs>
            <marker id="arrow" markerWidth="6" markerHeight="6"
                    refX="5" refY="3" orient="auto">
              <path d="M0,0 L6,3 L0,6 Z" fill="rgba(255,255,255,0.18)"/>
            </marker>
          </defs>
        </svg>

        <!-- empty state -->
        @if (!loading() && nodes().length === 0) {
          <div class="empty-state">
            <div class="empty-icon">⬡</div>
            <div class="empty-title">No graph data yet</div>
            <div class="empty-sub">Upload and index documents to see the knowledge graph</div>
          </div>
        }

        <!-- loading -->
        @if (loading()) {
          <div class="empty-state">
            <div class="spinner"></div>
            <div class="empty-sub" style="margin-top:12px">Building knowledge graph…</div>
          </div>
        }

        <!-- node detail panel -->
        @if (selected()) {
          <div class="detail-panel" @fadeIn>
            <div class="detail-header">
              <div class="detail-dot" [style.background]="typeColor(selected()!.type)"></div>
              <span class="detail-name">{{ selected()!.label }}</span>
              <button class="detail-close" (click)="selected.set(null)">✕</button>
            </div>
            <div class="detail-badge" [style.color]="typeColor(selected()!.type)">{{ selected()!.type | uppercase }}</div>
            @if (selected()!.description) {
              <p class="detail-desc">{{ selected()!.description }}</p>
            }
            <div class="detail-row"><span>Connections</span><strong>{{ selected()!.degree }}</strong></div>
            <div class="detail-row"><span>Documents</span><strong>{{ selected()!.doc_count }}</strong></div>
            @if (entityDetail()) {
              <div class="detail-section">
                <div class="detail-sec-label">Connected to</div>
                @for (n of entityDetail()!.neighbors; track n.id) {
                  <div class="neighbor-chip"
                       [style.border-color]="hexToRgba(typeColor(n.type), 0.35)"
                       [style.color]="typeColor(n.type)"
                       (click)="selectNodeById(n.id)">
                    <span class="neighbor-dot" [style.background]="typeColor(n.type)"></span>
                    {{ n.name }}
                  </div>
                }
              </div>
            }
          </div>
        }

        <!-- legend -->
        <div class="legend">
          @for (entry of legendEntries; track entry.type) {
            <div class="legend-row">
              <span class="legend-dot" [style.background]="entry.color"></span>
              {{ entry.label }}
            </div>
          }
        </div>
      </div>
    </div>
  `,
  styles: [`
    :host { display: block; flex: 1; height: 100%; overflow: hidden; }
    .gv-shell { display: flex; flex-direction: column; height: 100%; overflow: hidden; }

    /* toolbar */
    .toolbar {
      display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
      padding: 10px 16px; border-bottom: 1px solid var(--border-subtle); flex-shrink: 0;
      background: var(--bg-surface);
    }
    .search-wrap {
      position: relative; display: flex; align-items: center; flex-shrink: 0;
    }
    .search-icon {
      position: absolute; left: 9px; width: 14px; height: 14px;
      color: var(--text-tertiary); pointer-events: none;
    }
    .search-input {
      padding: 6px 28px 6px 30px; border-radius: var(--radius-md); width: 200px;
      background: var(--bg-elevated); border: 1px solid var(--border-normal);
      color: var(--text-primary); font-size: 12px; font-family: var(--font);
      outline: none;
      &:focus { border-color: var(--accent); }
      &::placeholder { color: var(--text-tertiary); }
    }
    .search-clear {
      position: absolute; right: 7px; background: none; border: none;
      color: var(--text-tertiary); cursor: pointer; font-size: 11px;
      &:hover { color: var(--text-primary); }
    }

    .filter-pills { display: flex; gap: 5px; flex-wrap: wrap; flex: 1; }
    .filter-pill {
      display: flex; align-items: center; gap: 5px;
      padding: 4px 10px; border-radius: 99px; font-size: 11px; font-weight: 500;
      border: 1px solid transparent; cursor: pointer; background: transparent;
      transition: all 0.15s; text-transform: capitalize;
      &:hover { border-color: var(--border-normal); background: var(--bg-elevated); }
    }
    .filter-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }

    .toolbar-right { display: flex; gap: 4px; flex-shrink: 0; margin-left: auto; }
    .tb-btn {
      width: 30px; height: 30px; border-radius: var(--radius-sm);
      background: var(--bg-elevated); border: 1px solid var(--border-normal);
      color: var(--text-secondary); font-size: 14px; cursor: pointer;
      display: flex; align-items: center; justify-content: center;
      transition: all 0.15s;
      &:hover { background: var(--bg-hover); color: var(--text-primary); }
    }

    /* canvas */
    .canvas-wrap { flex: 1; position: relative; overflow: hidden; }
    .graph-svg { width: 100%; height: 100%; background: var(--bg-base); }

    /* edges are styled via JS */
    :host ::ng-deep .link { stroke-opacity: 0.35; }
    :host ::ng-deep .link.highlighted { stroke-opacity: 0.85; }

    /* empty / loading */
    .empty-state {
      position: absolute; top: 50%; left: 50%; transform: translate(-50%,-50%);
      text-align: center; pointer-events: none;
    }
    .empty-icon { font-size: 48px; opacity: 0.15; margin-bottom: 12px; }
    .empty-title { font-size: 16px; color: var(--text-secondary); font-weight: 500; }
    .empty-sub   { font-size: 13px; color: var(--text-tertiary); margin-top: 6px; }
    .spinner {
      width: 32px; height: 32px; border: 2px solid var(--border-normal);
      border-top-color: var(--accent); border-radius: 50%; margin: 0 auto;
      animation: spin 0.8s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }

    /* detail panel */
    .detail-panel {
      position: absolute; top: 12px; right: 12px; width: 220px;
      background: var(--bg-elevated); border: 1px solid var(--border-normal);
      border-radius: var(--radius-lg); padding: 14px; overflow-y: auto;
      max-height: calc(100% - 24px); box-shadow: var(--shadow);
    }
    .detail-header {
      display: flex; align-items: center; gap: 8px; margin-bottom: 6px;
    }
    .detail-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
    .detail-name { font-size: 13px; font-weight: 600; color: var(--text-primary); flex: 1; }
    .detail-close {
      background: none; border: none; color: var(--text-tertiary);
      cursor: pointer; font-size: 12px; flex-shrink: 0;
      &:hover { color: var(--text-primary); }
    }
    .detail-badge {
      font-size: 10px; font-weight: 700; letter-spacing: 0.06em;
      margin-bottom: 10px;
    }
    .detail-desc {
      font-size: 12px; color: var(--text-secondary); line-height: 1.6;
      margin-bottom: 10px;
    }
    .detail-row {
      display: flex; justify-content: space-between;
      font-size: 12px; padding: 4px 0; border-bottom: 1px solid var(--border-subtle);
      span { color: var(--text-secondary); }
      strong { color: var(--text-primary); font-weight: 600; }
      &:last-of-type { border-bottom: none; }
    }
    .detail-section { margin-top: 12px; }
    .detail-sec-label {
      font-size: 10px; font-weight: 600; color: var(--text-tertiary);
      letter-spacing: 0.06em; text-transform: uppercase; margin-bottom: 6px;
    }
    .neighbor-chip {
      display: inline-flex; align-items: center; gap: 5px;
      padding: 3px 8px; border-radius: 99px; border: 1px solid;
      font-size: 11px; cursor: pointer; margin: 2px 2px;
      transition: opacity 0.15s;
      &:hover { opacity: 0.8; }
    }
    .neighbor-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }

    /* legend */
    .legend {
      position: absolute; bottom: 14px; left: 14px;
      background: var(--bg-elevated); border: 1px solid var(--border-subtle);
      border-radius: var(--radius-md); padding: 10px 14px;
      display: flex; flex-direction: column; gap: 5px;
    }
    .legend-row { display: flex; align-items: center; gap: 7px; font-size: 11px; color: var(--text-secondary); }
    .legend-dot { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }
  `],
})
export class GraphViewComponent implements OnInit, OnDestroy, AfterViewInit {
  @ViewChild('svgEl') svgRef!: ElementRef<SVGElement>;

  nodes    = signal<D3Node[]>([]);
  loading  = signal(false);
  selected = signal<GraphNode | null>(null);
  entityDetail = signal<EntityDetail | null>(null);
  activeFilters = signal<Set<string>>(new Set());

  searchTerm = '';
  entityTypes: string[] = [];

  legendEntries = Object.entries(TYPE_COLOR)
    .filter(([t]) => !['date'].includes(t))
    .map(([type, color]) => ({ type, label: type, color }));

  private svg!: d3.Selection<SVGGElement, unknown, null, undefined>;
  private zoom!: d3.ZoomBehavior<SVGElement, unknown>;
  private sim!: d3.Simulation<D3Node, D3Link>;
  private allNodes: D3Node[] = [];
  private allLinks: D3Link[] = [];
  private sub?: Subscription;

  constructor(private api: ApiService) {}

  ngOnInit() { this.loadGraph(); }

  ngAfterViewInit() { this.initSvg(); }

  ngOnDestroy() {
    this.sub?.unsubscribe();
    this.sim?.stop();
  }

  typeColor = typeColor;

  hexToRgba(hex: string, alpha: number): string {
    const r = parseInt(hex.slice(1,3), 16);
    const g = parseInt(hex.slice(3,5), 16);
    const b = parseInt(hex.slice(5,7), 16);
    return `rgba(${r},${g},${b},${alpha})`;
  }

  // ── init ──────────────────────────────────────────────────────────────────

  private initSvg() {
    const el = this.svgRef.nativeElement;
    const root = d3.select(el);
    this.zoom = d3.zoom<SVGElement, unknown>()
      .scaleExtent([0.05, 4])
      .on('zoom', e => this.svg.attr('transform', e.transform));
    root.call(this.zoom as any);
    this.svg = root.append('g');
  }

  // ── data ──────────────────────────────────────────────────────────────────

  loadGraph() {
    this.loading.set(true);
    this.api.getGraph().subscribe({
      next: (data: GraphData) => {
        this.loading.set(false);
        const typeSet = new Set<string>();
        this.allNodes = data.nodes.map(n => ({
          ...n, x: 0, y: 0, vx: 0, vy: 0,
        }));
        this.allNodes.forEach(n => typeSet.add(n.type.toLowerCase()));
        this.entityTypes = Array.from(typeSet).sort();
        this.allLinks = data.edges
          .map(e => {
            const src = this.allNodes.find(n => n.id === e.source);
            const tgt = this.allNodes.find(n => n.id === e.target);
            return src && tgt ? { source: src, target: tgt, relation: e.relation, weight: e.weight } : null;
          })
          .filter(Boolean) as D3Link[];
        this.nodes.set(this.allNodes);
        this.renderGraph();
      },
      error: () => this.loading.set(false),
    });
  }

  // ── filter / search ───────────────────────────────────────────────────────

  toggleFilter(type: string) {
    const s = new Set(this.activeFilters());
    s.has(type) ? s.delete(type) : s.add(type);
    this.activeFilters.set(s);
    this.applyFilters();
  }

  onSearch(term: string) { this.applyFilters(); }

  private applyFilters() {
    const filters = this.activeFilters();
    const term = this.searchTerm.toLowerCase();
    let visible = this.allNodes.filter(n => {
      const typeOk = filters.size === 0 || filters.has(n.type.toLowerCase());
      const searchOk = !term || n.label.toLowerCase().includes(term) || n.description?.toLowerCase().includes(term);
      return typeOk && searchOk;
    });
    const visibleIds = new Set(visible.map(n => n.id));
    const visLinks = this.allLinks.filter(
      l => visibleIds.has((l.source as D3Node).id) && visibleIds.has((l.target as D3Node).id)
    );
    this.renderGraph(visible, visLinks);
    // highlight search matches
    if (term) {
      d3.selectAll<SVGCircleElement, D3Node>('.node-circle')
        .attr('opacity', d => d.label.toLowerCase().includes(term) ? 1 : 0.2);
    } else {
      d3.selectAll('.node-circle').attr('opacity', 1);
    }
  }

  // ── render ────────────────────────────────────────────────────────────────

  private renderGraph(nodes = this.allNodes, links = this.allLinks) {
    if (!this.svg) return;
    this.svg.selectAll('*').remove();
    if (nodes.length === 0) return;

    const el = this.svgRef.nativeElement;
    const W = el.clientWidth || 800;
    const H = el.clientHeight || 600;

    // place nodes randomly if not positioned
    nodes.forEach(n => {
      if (!n.x) { n.x = W / 2 + (Math.random() - 0.5) * 400; }
      if (!n.y) { n.y = H / 2 + (Math.random() - 0.5) * 400; }
    });

    // glow filter
    const defs = this.svg.append('defs');
    defs.append('filter').attr('id', 'glow')
      .html(`<feGaussianBlur stdDeviation="3" result="blur"/>
             <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>`);

    // links
    const linkG = this.svg.append('g').attr('class', 'links');
    const link = linkG.selectAll<SVGLineElement, D3Link>('line')
      .data(links).join('line')
      .attr('class', 'link')
      .attr('stroke', 'rgba(255,255,255,0.12)')
      .attr('stroke-width', (d: D3Link) => Math.sqrt(d.weight))
      .attr('marker-end', 'url(#arrow)');

    // nodes
    const nodeG = this.svg.append('g').attr('class', 'nodes');
    const node = nodeG.selectAll<SVGGElement, D3Node>('g')
      .data(nodes, (d: D3Node) => d.id).join('g')
      .attr('class', 'node')
      .style('cursor', 'pointer')
      .call(
        d3.drag<SVGGElement, D3Node>()
          .on('start', (event, d) => {
            if (!event.active) this.sim?.alphaTarget(0.3).restart();
            d.fx = d.x; d.fy = d.y;
          })
          .on('drag', (event, d) => { d.fx = event.x; d.fy = event.y; })
          .on('end',  (event, d) => {
            if (!event.active) this.sim?.alphaTarget(0);
            d.fx = null; d.fy = null;
          }) as any
      )
      .on('click', (_event, d) => this.onNodeClick(d));

    node.append('circle')
      .attr('class', 'node-circle')
      .attr('r', (d: D3Node) => nodeRadius(d.degree))
      .attr('fill', (d: D3Node) => this.hexToRgba(typeColor(d.type), 0.15))
      .attr('stroke', (d: D3Node) => typeColor(d.type))
      .attr('stroke-width', 1.5)
      .attr('filter', 'url(#glow)');

    node.append('text')
      .attr('text-anchor', 'middle')
      .attr('dominant-baseline', 'central')
      .attr('font-size', (d: D3Node) => Math.max(8, nodeRadius(d.degree) * 0.6))
      .attr('font-family', 'Inter, system-ui, sans-serif')
      .attr('fill', (d: D3Node) => typeColor(d.type))
      .attr('pointer-events', 'none')
      .text((d: D3Node) => d.label.split(' ')[0]);

    // tooltip
    const tooltip = d3.select('body').select<HTMLDivElement>('.d3-tooltip');
    const tip = tooltip.empty()
      ? d3.select('body').append('div').attr('class', 'd3-tooltip').style('opacity', 0)
        .style('position', 'fixed').style('background', 'var(--bg-elevated)')
        .style('border', '1px solid var(--border-normal)').style('border-radius', '8px')
        .style('padding', '8px 12px').style('font-size', '12px').style('pointer-events', 'none')
        .style('color', 'var(--text-primary)').style('font-family', 'Inter, sans-serif')
        .style('box-shadow', 'var(--shadow)').style('z-index', '9999').style('max-width', '200px')
      : tooltip;

    node
      .on('mouseenter', (event, d) => {
        tip.transition().duration(100).style('opacity', 1);
        tip.html(`<strong>${d.label}</strong><br><span style="color:${typeColor(d.type)}">${d.type}</span>`
          + (d.description ? `<br><span style="color:#8b9ab8;font-size:11px">${d.description.slice(0,80)}…</span>` : ''));
        link.attr('stroke', (l: D3Link) =>
          l.source.id === d.id || l.target.id === d.id
            ? typeColor(d.type)
            : 'rgba(255,255,255,0.06)'
        ).attr('stroke-width', (l: D3Link) =>
          l.source.id === d.id || l.target.id === d.id ? 2 : Math.sqrt((l as D3Link).weight)
        );
      })
      .on('mousemove', event => {
        tip.style('left', (event.clientX + 12) + 'px')
           .style('top',  (event.clientY - 8)  + 'px');
      })
      .on('mouseleave', () => {
        tip.transition().duration(200).style('opacity', 0);
        link.attr('stroke', 'rgba(255,255,255,0.12)')
            .attr('stroke-width', (l: D3Link) => Math.sqrt(l.weight));
      });

    // simulation
    if (this.sim) this.sim.stop();
    this.sim = d3.forceSimulation<D3Node>(nodes)
      .force('link', d3.forceLink<D3Node, D3Link>(links)
        .id((d: D3Node) => d.id).distance(90).strength(0.5))
      .force('charge', d3.forceManyBody().strength(-280))
      .force('center', d3.forceCenter(W / 2, H / 2).strength(0.05))
      .force('collision', d3.forceCollide<D3Node>().radius((d: D3Node) => nodeRadius(d.degree) + 6))
      .on('tick', () => {
        link
          .attr('x1', (d: D3Link) => (d.source as D3Node).x)
          .attr('y1', (d: D3Link) => (d.source as D3Node).y)
          .attr('x2', (d: D3Link) => (d.target as D3Node).x)
          .attr('y2', (d: D3Link) => (d.target as D3Node).y);
        node.attr('transform', (d: D3Node) => `translate(${d.x},${d.y})`);
      });
  }

  // ── node click ────────────────────────────────────────────────────────────

  private onNodeClick(d: D3Node) {
    this.selected.set(d);
    this.entityDetail.set(null);
    this.api.getEntity(d.id).subscribe({
      next: det => this.entityDetail.set(det),
      error: () => {},
    });
    // highlight connected edges
    d3.selectAll<SVGLineElement, D3Link>('.link')
      .attr('stroke', (l: D3Link) =>
        l.source.id === d.id || l.target.id === d.id
          ? typeColor(d.type)
          : 'rgba(255,255,255,0.06)'
      )
      .attr('stroke-width', (l: D3Link) =>
        l.source.id === d.id || l.target.id === d.id ? 2.5 : Math.sqrt(l.weight)
      );
    // highlight node
    d3.selectAll<SVGCircleElement, D3Node>('.node-circle')
      .attr('stroke-width', (n: D3Node) => n.id === d.id ? 3 : 1.5)
      .attr('opacity',      (n: D3Node) => n.id === d.id ? 1 : 0.55);
  }

  selectNodeById(id: string) {
    const n = this.allNodes.find(n => n.id === id);
    if (n) this.onNodeClick(n);
  }

  // ── zoom helpers ──────────────────────────────────────────────────────────

  zoomBy(factor: number) {
    d3.select(this.svgRef.nativeElement)
      .transition().duration(250)
      .call(this.zoom.scaleBy as any, factor);
  }

  fitView() {
    if (!this.allNodes.length) return;
    const el = this.svgRef.nativeElement;
    const W = el.clientWidth, H = el.clientHeight;
    const xs = this.allNodes.map(n => n.x), ys = this.allNodes.map(n => n.y);
    const x0 = Math.min(...xs), x1 = Math.max(...xs);
    const y0 = Math.min(...ys), y1 = Math.max(...ys);
    const pad = 60;
    const scale = Math.min((W - pad) / (x1 - x0 || 1), (H - pad) / (y1 - y0 || 1), 2);
    const tx = W / 2 - scale * (x0 + x1) / 2;
    const ty = H / 2 - scale * (y0 + y1) / 2;
    d3.select(this.svgRef.nativeElement)
      .transition().duration(400)
      .call(this.zoom.transform as any, d3.zoomIdentity.translate(tx, ty).scale(scale));
  }
}
