import { Component, OnInit, signal, ViewChild, ElementRef, AfterViewChecked, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService, ChatMessage, QueryResponse } from '../services/api.service';
import { CaseContextService } from '../services/case-context.service';
import { describeApiError } from '../services/api-errors';
import { withAlpha } from '../shared/format';

interface Message {
  role: 'user' | 'ai';
  text?: string;
  response?: QueryResponse;
  loading?: boolean;
  author?: string;   // who asked; set for turns loaded from the case history
  ts: number;
}

type Method = 'hybrid' | 'graphrag' | 'raptor' | 'hippo';

const METHOD_LABELS: Record<Method, { label: string; color: string; desc: string }> = {
  hybrid:   { label: 'Hybrid',    color: 'var(--accent)', desc: 'GraphRAG + RAPTOR + HiPPO' },
  graphrag: { label: 'GraphRAG',  color: 'var(--green)', desc: 'Knowledge graph communities' },
  raptor:   { label: 'RAPTOR',    color: 'var(--purple)', desc: 'Tree-based summarisation' },
  hippo:    { label: 'HiPPO',     color: 'var(--amber)', desc: 'Hierarchical passage pooling' },
};

@Component({
  selector: 'app-qa-panel',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="qa-shell">
      @if (messages().length) {
        <div class="chat-tools">
          <span>Case chat history · shared with everyone on this case</span>
          @if (ctx.isLead()) {
            <button class="clear-btn" (click)="clearHistory()" [disabled]="isLoading()">Clear history</button>
          }
        </div>
      }
      <!-- messages -->
      <div class="messages" #msgContainer>
        @if (messages().length === 0) {
          <div class="welcome">
            <div class="welcome-icon">💬</div>
            <div class="welcome-title">Ask anything about this case's evidence</div>
            <div class="welcome-sub">The AI will search the knowledge graph, RAPTOR tree, and HiPPO passages to answer your question.</div>
            <div class="suggestion-grid">
              @for (s of suggestions(); track s) {
                <button class="sugg-card" (click)="useQuery(s)">{{ s }}</button>
              }
            </div>
          </div>
        }

        @for (msg of messages(); track msg.ts) {
          @if (msg.role === 'user') {
            <div class="msg user-msg">
              <div class="msg-avatar user-av">U</div>
              <div class="msg-bubble user-bubble" [title]="msg.author ? 'Asked by ' + msg.author : ''">{{ msg.text }}</div>
            </div>
          }
          @if (msg.role === 'ai') {
            <div class="msg ai-msg">
              <div class="msg-avatar ai-av">AI</div>
              <div class="msg-bubble ai-bubble">
                @if (msg.loading) {
                  <div class="typing">
                    <span class="dot"></span><span class="dot"></span><span class="dot"></span>
                  </div>
                } @else if (msg.response) {
                  <p class="ai-answer">{{ msg.response.answer }}</p>

                  <!-- entity chips -->
                  @if (msg.response.entities.length) {
                    <div class="chips-row">
                      @for (ent of msg.response.entities; track ent.id) {
                        <span class="chip chip-{{ ent.type }}">
                          {{ ent.name }}
                        </span>
                      }
                    </div>
                  }

                  <!-- sources -->
                  @if (msg.response.sources.length) {
                    <div class="sources-row">
                      @for (src of msg.response.sources; track src) {
                        <span class="cite-tag">📄 {{ src | slice:0:30 }}</span>
                      }
                    </div>
                  }

                  <!-- meta row -->
                  <div class="ai-meta">
                    <span class="method-badge"
                          [style.color]="methodInfo(msg.response.method).color"
                          [style.border-color]="withAlpha(methodInfo(msg.response.method).color, 0.3)"
                          [style.background]="withAlpha(methodInfo(msg.response.method).color, 0.08)">
                      {{ methodInfo(msg.response.method).label }}
                    </span>
                    <span class="conf-badge" [class.conf-high]="msg.response.confidence > 0.7">
                      {{ (msg.response.confidence * 100).toFixed(0) }}% confident
                    </span>
                    @if (msg.response.reasoning) {
                      <button class="reasoning-btn"
                              (click)="toggleReasoning(msg)"
                              [class.open]="expandedMsgs.has(msg.ts)">
                        Reasoning {{ expandedMsgs.has(msg.ts) ? '▲' : '▼' }}
                      </button>
                    }
                  </div>
                  @if (msg.response.reasoning && expandedMsgs.has(msg.ts)) {
                    <div class="reasoning-box">{{ msg.response.reasoning }}</div>
                  }
                }
              </div>
            </div>
          }
        }
      </div>

      <!-- suggestions strip when has messages -->
      @if (messages().length > 0 && suggestions().length) {
        <div class="sugg-strip">
          @for (s of suggestions(); track s) {
            <button class="sugg-chip" (click)="useQuery(s)">{{ s }}</button>
          }
        </div>
      }

      <!-- input -->
      <div class="input-area">
        <!-- method selector -->
        <div class="method-selector">
          @for (m of methods; track m) {
            <button class="method-btn"
                    [class.active]="activeMethod() === m"
                    [style.color]="activeMethod() === m ? methodInfo(m).color : ''"
                    [style.border-color]="activeMethod() === m ? withAlpha(methodInfo(m).color, 0.4) : ''"
                    [style.background]="activeMethod() === m ? withAlpha(methodInfo(m).color, 0.1) : ''"
                    [title]="methodInfo(m).desc"
                    (click)="activeMethod.set(m)">
              {{ methodInfo(m).label }}
            </button>
          }
        </div>

        <div class="input-row">
          <input #inputEl class="q-input" [(ngModel)]="inputText"
                 placeholder="Ask anything about your documents…"
                 (keydown.enter)="send()"
                 [disabled]="isLoading()"/>
          <button class="send-btn" (click)="send()" [disabled]="isLoading() || !inputText.trim()">
            @if (isLoading()) {
              <span class="send-spinner"></span>
            } @else {
              <svg viewBox="0 0 20 20" fill="currentColor" width="16" height="16">
                <path d="M10.894 2.553a1 1 0 0 0-1.788 0l-7 14a1 1 0 0 0 1.169 1.409l5-1.429A1 1 0 0 0 9 15.571V11a1 1 0 1 1 2 0v4.571a1 1 0 0 0 .725.962l5 1.428a1 1 0 0 0 1.17-1.408l-7-14z"/>
              </svg>
            }
          </button>
        </div>
      </div>
    </div>
  `,
  styles: [`
    :host { display: block; flex: 1; height: 100%; overflow: hidden; }
    .qa-shell { display: flex; flex-direction: column; height: 100%; overflow: hidden; }

    .chat-tools {
      display: flex; align-items: center; justify-content: space-between; gap: 12px;
      padding: 8px 24px; font-size: 11px; color: var(--text-tertiary);
      border-bottom: 1px solid var(--border-subtle); flex-shrink: 0;
    }
    .clear-btn {
      background: none; border: 1px solid var(--border-normal); border-radius: var(--radius-sm);
      color: var(--text-secondary); font-size: 11px; padding: 3px 9px; cursor: pointer;
      &:hover:not(:disabled) { color: var(--red); border-color: var(--red); }
    }

    /* messages */
    .messages {
      flex: 1; overflow-y: auto; padding: 20px 24px; display: flex; flex-direction: column; gap: 18px;
    }

    /* welcome */
    .welcome {
      display: flex; flex-direction: column; align-items: center;
      padding: 40px 20px; text-align: center; gap: 12px; margin: auto 0;
    }
    .welcome-icon  { font-size: 40px; opacity: 0.4; }
    .welcome-title { font-size: 18px; font-weight: 600; color: var(--text-primary); }
    .welcome-sub   { font-size: 13px; color: var(--text-secondary); max-width: 460px; line-height: 1.6; }
    .suggestion-grid {
      display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
      gap: 8px; width: 100%; max-width: 560px; margin-top: 8px;
    }
    .sugg-card {
      padding: 10px 14px; border-radius: var(--radius-md);
      background: var(--bg-elevated); border: 1px solid var(--border-normal);
      color: var(--text-secondary); font-size: 12px; cursor: pointer; text-align: left;
      transition: all 0.15s; line-height: 1.5;
      &:hover { border-color: var(--accent); color: var(--text-primary); background: var(--accent-dim); }
    }

    .chat-tools {
      display: flex; align-items: center; justify-content: space-between; gap: 12px;
      padding: 8px 24px; font-size: 11px; color: var(--text-tertiary);
      border-bottom: 1px solid var(--border-subtle); flex-shrink: 0;
    }
    .clear-btn {
      background: none; border: 1px solid var(--border-normal); border-radius: var(--radius-sm);
      color: var(--text-secondary); font-size: 11px; padding: 3px 9px; cursor: pointer;
      &:hover:not(:disabled) { color: var(--red); border-color: var(--red); }
    }

    /* messages */
    .msg { display: flex; gap: 10px; align-items: flex-start; }
    .user-msg { flex-direction: row-reverse; }
    .msg-avatar {
      width: 30px; height: 30px; border-radius: 50%; flex-shrink: 0;
      display: flex; align-items: center; justify-content: center;
      font-size: 11px; font-weight: 700;
    }
    .user-av { background: var(--accent-dim); color: var(--accent); }
    .ai-av   { background: rgba(52,211,153,0.12); color: var(--green); }
    .msg-bubble { max-width: 76%; }
    .user-bubble {
      background: var(--accent-dim); border: 1px solid rgba(79,135,255,0.2);
      border-radius: var(--radius-md) 4px var(--radius-md) var(--radius-md);
      padding: 10px 14px; font-size: 13px; color: var(--text-primary); line-height: 1.6;
    }
    .ai-bubble {
      background: var(--bg-elevated); border: 1px solid var(--border-subtle);
      border-radius: 4px var(--radius-md) var(--radius-md) var(--radius-md);
      padding: 12px 16px; display: flex; flex-direction: column; gap: 10px;
    }
    .ai-answer { font-size: 13px; color: var(--text-primary); line-height: 1.7; }
    .chips-row { display: flex; flex-wrap: wrap; gap: 5px; }
    .sources-row { display: flex; flex-wrap: wrap; gap: 5px; }
    .ai-meta { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .method-badge {
      font-size: 10px; font-weight: 600; padding: 2px 8px; border-radius: 99px; border: 1px solid;
    }
    .conf-badge {
      font-size: 10px; color: var(--text-tertiary);
      &.conf-high { color: var(--green); }
    }
    .reasoning-btn {
      font-size: 10px; background: none; border: none; color: var(--text-tertiary);
      cursor: pointer; transition: color 0.15s; margin-left: auto;
      &:hover { color: var(--text-primary); }
    }
    .reasoning-box {
      background: var(--bg-base); border: 1px solid var(--border-subtle);
      border-radius: var(--radius-sm); padding: 10px 12px;
      font-size: 12px; color: var(--text-secondary); line-height: 1.6; font-style: italic;
    }

    /* typing */
    .typing { display: flex; gap: 5px; align-items: center; padding: 4px 0; }
    .dot {
      width: 7px; height: 7px; border-radius: 50%; background: var(--green);
      animation: bounce 1.3s ease-in-out infinite;
      &:nth-child(2) { animation-delay: 0.18s; }
      &:nth-child(3) { animation-delay: 0.36s; }
    }
    @keyframes bounce { 0%,80%,100%{transform:translateY(0)} 40%{transform:translateY(-6px)} }

    /* sugg strip */
    .sugg-strip {
      padding: 8px 16px; display: flex; gap: 6px; overflow-x: auto; flex-shrink: 0;
      border-top: 1px solid var(--border-subtle);
    }
    .sugg-chip {
      padding: 5px 12px; border-radius: 99px; border: 1px solid var(--border-normal);
      background: var(--bg-elevated); color: var(--text-secondary); font-size: 11px;
      cursor: pointer; white-space: nowrap; flex-shrink: 0; transition: all 0.15s;
      &:hover { border-color: var(--accent); color: var(--accent); background: var(--accent-dim); }
    }

    /* input area */
    .input-area {
      padding: 12px 16px 16px; border-top: 1px solid var(--border-subtle);
      background: var(--bg-surface); flex-shrink: 0; display: flex; flex-direction: column; gap: 8px;
    }
    .method-selector { display: flex; gap: 4px; }
    .method-btn {
      padding: 4px 12px; border-radius: 99px; border: 1px solid var(--border-subtle);
      background: transparent; color: var(--text-tertiary); font-size: 11px; font-weight: 500;
      cursor: pointer; transition: all 0.15s;
      &:hover { border-color: var(--border-normal); color: var(--text-secondary); }
    }
    .input-row { display: flex; gap: 8px; }
    .q-input {
      flex: 1; padding: 10px 14px; border-radius: var(--radius-md);
      background: var(--bg-elevated); border: 1px solid var(--border-normal);
      color: var(--text-primary); font-size: 13px; font-family: var(--font); outline: none;
      transition: border-color 0.15s;
      &:focus { border-color: var(--accent); }
      &::placeholder { color: var(--text-tertiary); }
      &:disabled { opacity: 0.5; }
    }
    .send-btn {
      width: 40px; height: 40px; border-radius: var(--radius-md);
      background: var(--accent); border: none; color: white; cursor: pointer;
      display: flex; align-items: center; justify-content: center; flex-shrink: 0;
      transition: background 0.15s;
      &:hover:not(:disabled) { background: var(--accent-hover); }
      &:disabled { opacity: 0.4; cursor: not-allowed; }
    }
    .send-spinner {
      width: 14px; height: 14px; border: 2px solid rgba(255,255,255,0.3);
      border-top-color: white; border-radius: 50%;
      animation: spin 0.7s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
  `],
})
export class QaPanelComponent implements OnInit, AfterViewChecked {
  @ViewChild('msgContainer') msgContainer!: ElementRef<HTMLDivElement>;

  messages   = signal<Message[]>([]);
  suggestions = signal<string[]>([]);
  isLoading  = signal(false);
  activeMethod = signal<Method>('hybrid');
  expandedMsgs = new Set<number>();

  inputText = '';
  methods: Method[] = ['hybrid', 'graphrag', 'raptor', 'hippo'];

  private shouldScroll = false;
  readonly ctx = inject(CaseContextService);

  constructor(private api: ApiService) {}

  ngOnInit() {
    this.loadHistory();
    this.api.getSuggestions(this.ctx.caseId()).subscribe({
      next: r => this.suggestions.set(r.suggestions),
      error: () => this.suggestions.set([
        'Summarise the key themes',
        'What relationships exist between the main entities?',
        'What are the most important facts?',
      ]),
    });
  }

  ngAfterViewChecked() {
    if (this.shouldScroll) {
      this.scrollBottom();
      this.shouldScroll = false;
    }
  }

  methodInfo(m: string) {
    return METHOD_LABELS[m as Method] ?? METHOD_LABELS['hybrid'];
  }

  withAlpha = withAlpha;

  toggleReasoning(msg: Message) {
    if (this.expandedMsgs.has(msg.ts)) {
      this.expandedMsgs.delete(msg.ts);
    } else {
      this.expandedMsgs.add(msg.ts);
    }
  }

  useQuery(q: string) {
    this.inputText = q;
    this.send();
  }

  send() {
    const q = this.inputText.trim();
    if (!q || this.isLoading()) return;
    this.inputText = '';

    const userMsg: Message = { role: 'user', text: q, ts: Date.now() };
    const aiMsg: Message   = { role: 'ai', loading: true, ts: Date.now() + 1 };

    this.messages.update(msgs => [...msgs, userMsg, aiMsg]);
    this.isLoading.set(true);
    this.shouldScroll = true;

    this.api.query(this.ctx.caseId(), q, this.activeMethod(), 6).subscribe({
      next: resp => {
        this.messages.update(msgs =>
          msgs.map(m => m === aiMsg ? { ...m, loading: false, response: resp } : m)
        );
        this.isLoading.set(false);
        this.shouldScroll = true;
      },
      error: err => {
        const errResp: QueryResponse = {
          answer: describeApiError(err, 'An error occurred. Make sure documents are indexed and the API key is set.'),
          entities: [], sources: [], confidence: 0, reasoning: '', method: this.activeMethod(),
        };
        this.messages.update(msgs =>
          msgs.map(m => m === aiMsg ? { ...m, loading: false, response: errResp } : m)
        );
        this.isLoading.set(false);
        this.shouldScroll = true;
      },
    });
  }

  /** Turn the stored history into user/AI message pairs. */
  private loadHistory() {
    this.api.getChatHistory(this.ctx.caseId()).subscribe({
      next: history => {
        this.messages.set(history.flatMap(h => this.toMessages(h)));
        this.shouldScroll = true;
      },
      error: () => {},
    });
  }

  private toMessages(h: ChatMessage): Message[] {
    const ts = Date.parse(h.created_at);
    const response: QueryResponse = {
      answer: h.answer, entities: h.entities, sources: h.sources,
      confidence: h.confidence, reasoning: h.reasoning, method: h.method,
    };
    return [
      { role: 'user', text: h.question, author: h.user_name ?? undefined, ts },
      { role: 'ai', response, ts: ts + 1 },
    ];
  }

  clearHistory() {
    if (!confirm('Clear the chat history of this case for everyone?')) return;
    this.api.clearChatHistory(this.ctx.caseId()).subscribe({
      next: () => this.messages.set([]),
      error: () => {},
    });
  }

  private scrollBottom() {
    const el = this.msgContainer?.nativeElement;
    if (el) el.scrollTop = el.scrollHeight;
  }
}
