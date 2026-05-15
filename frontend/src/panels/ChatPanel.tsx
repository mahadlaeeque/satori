import { useEffect, useRef, useState } from "react";
import {
  useAskSatori, useConversation, useConversations,
  useDeleteConversation, useRenameConversation,
} from "@/api/hooks";
import type { ChatMessage } from "@/api/types";
import { Send, Trash2, Pencil, MessageCircle, Plus, X, ClipboardList } from "lucide-react";
import clsx from "clsx";

function uid() {
  return "conv_" + Date.now() + "_" + Math.random().toString(36).slice(2, 8);
}

export default function ChatPanel() {
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages]   = useState<ChatMessage[]>([]);
  const [input, setInput]         = useState("");
  const [historyOpen, setHistoryOpen] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);

  const { data: conversations } = useConversations();
  const { data: activeConv }    = useConversation(conversationId);
  const askMutation             = useAskSatori();
  const deleteMutation          = useDeleteConversation();
  const renameMutation          = useRenameConversation();

  // When the user switches to a different historical conversation, load its
  // messages once. Local writes after that stay local — no source-swap glitch.
  const lastLoadedConvId = useRef<string | null>(null);
  useEffect(() => {
    if (!conversationId) {
      setMessages([]);
      lastLoadedConvId.current = null;
      return;
    }
    if (activeConv?.id === conversationId && lastLoadedConvId.current !== conversationId) {
      setMessages(activeConv.messages ?? []);
      lastLoadedConvId.current = conversationId;
    }
  }, [conversationId, activeConv?.id, activeConv?.messages]);

  // Smooth scroll on any message change
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages.length, askMutation.isPending]);

  async function handleSend() {
    const q = input.trim();
    if (!q || askMutation.isPending) return;
    const cid = conversationId ?? uid();
    setMessages(prev => [...prev, { role: "user", content: q, timestamp: new Date().toISOString() }]);
    setInput("");
    try {
      const resp = await askMutation.mutateAsync({ question: q, conversation_id: cid });
      if (!conversationId) setConversationId(resp.conversation_id);
      lastLoadedConvId.current = resp.conversation_id;
      setMessages(prev => [...prev, { role: "bot", content: resp.answer, timestamp: new Date().toISOString() }]);
    } catch {
      setMessages(prev => [...prev, {
        role: "bot",
        content: "Sorry, that request failed. Please try again.",
        timestamp: new Date().toISOString(),
      }]);
    }
  }

  function startNewChat() {
    setConversationId(null);
    setMessages([]);
    setInput("");
    lastLoadedConvId.current = null;
  }

  async function handleRename(id: string) {
    const t = prompt("Rename conversation");
    if (t && t.trim()) renameMutation.mutate({ id, title: t.trim() });
  }

  async function handleDelete(id: string) {
    if (!confirm("Delete this conversation?")) return;
    await deleteMutation.mutateAsync(id);
    if (id === conversationId) startNewChat();
  }

  const groups = groupByDate(conversations ?? []);

  return (
    <div className="h-full flex">
      {/* Chat area */}
      <div className="flex-1 flex flex-col bg-white dark:bg-satori-ink">
        <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-6 space-y-4">
          {messages.length === 0 && !askMutation.isPending ? (
            <Welcome onPrompt={s => setInput(s)} />
          ) : (
            messages.map((m, i) => <Bubble key={i} message={m} />)
          )}
          {askMutation.isPending && <TypingBubble />}
        </div>

        {/* Composer */}
        <div className="bg-white dark:bg-satori-ink p-5 border-t border-slate-200 dark:border-slate-800">
          <div className="max-w-3xl mx-auto">
            <div className="flex gap-2 items-end card p-1.5">
              <textarea
                className="flex-1 px-3 py-2 bg-transparent text-sm focus:outline-none resize-none placeholder:text-slate-500 dark:placeholder:text-slate-400 text-slate-800 dark:text-slate-100"
                rows={1}
                placeholder="Ask about attendance, timesheets, or resource allocation…"
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={e => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    handleSend();
                  }
                }}
              />
              <button
                onClick={handleSend}
                disabled={!input.trim() || askMutation.isPending}
                className="w-10 h-10 rounded-lg bg-gradient-to-br from-satori-green to-satori-teal text-white flex items-center justify-center disabled:opacity-40 hover:opacity-90 transition"
              >
                <Send className="w-4 h-4" />
              </button>
            </div>
            <div className="text-center text-[11px] text-slate-500 dark:text-slate-400 mt-3">
              Satori queries your live BigQuery data — speak in English or Urdu
            </div>
          </div>
        </div>
      </div>

      {/* Right-side history */}
      {historyOpen && (
        <aside className="w-80 shrink-0 border-l border-slate-200 dark:border-slate-800 bg-white dark:bg-satori-paper flex flex-col">
          <div className="px-5 py-4 border-b border-slate-200 dark:border-slate-800 flex items-center justify-between">
            <h3 className="font-sans text-base font-semibold text-slate-900 dark:text-slate-100">Chat History</h3>
            <button onClick={() => setHistoryOpen(false)} className="text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200">
              <X className="w-4 h-4" />
            </button>
          </div>
          <div className="p-3">
            <button onClick={startNewChat} className="btn-primary w-full justify-center bg-gradient-to-br from-satori-green/10 to-satori-teal/10 text-satori-teal shadow-none border border-satori-green/30 hover:from-satori-green/15 dark:from-satori-green/20 dark:to-satori-teal/20 dark:text-satori-green dark:border-satori-green/40">
              <Plus className="w-4 h-4" /> New Conversation
            </button>
          </div>

          <div className="flex-1 overflow-y-auto px-3">
            {!conversations?.length ? (
              <div className="px-3 py-8 text-center text-xs text-slate-500 dark:text-slate-400">No conversations yet. Start chatting to build your history.</div>
            ) : (
              Object.entries(groups).map(([label, items]) =>
                items.length ? (
                  <div key={label} className="mb-3">
                    <div className="px-2 py-1.5 text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">{label}</div>
                    {items.map(c => (
                      <div
                        key={c.id}
                        onClick={() => setConversationId(c.id)}
                        className={clsx(
                          "group px-3 py-2.5 rounded-lg cursor-pointer flex items-start gap-2 transition-colors mb-0.5",
                          c.id === conversationId
                            ? "bg-satori-green/10 dark:bg-satori-green/15 border-l-2 border-l-satori-green pl-2.5"
                            : "hover:bg-slate-50 dark:hover:bg-slate-800/60",
                        )}
                      >
                        <MessageCircle className="w-3.5 h-3.5 text-satori-green/70 shrink-0 mt-0.5" />
                        <div className="flex-1 min-w-0">
                          <div className="text-xs font-medium text-slate-800 dark:text-slate-200 truncate">{c.title}</div>
                        </div>
                        <div className="opacity-0 group-hover:opacity-100 flex gap-1 transition-opacity">
                          <button onClick={e => { e.stopPropagation(); handleRename(c.id); }} className="text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200">
                            <Pencil className="w-3 h-3" />
                          </button>
                          <button onClick={e => { e.stopPropagation(); handleDelete(c.id); }} className="text-slate-500 hover:text-red-500 dark:text-slate-400">
                            <Trash2 className="w-3 h-3" />
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : null,
              )
            )}
          </div>
        </aside>
      )}

      {!historyOpen && (
        <button
          onClick={() => setHistoryOpen(true)}
          className="fixed top-1/2 right-4 -translate-y-1/2 z-30 w-8 h-12 rounded-l-lg bg-white dark:bg-satori-paper border border-slate-200 dark:border-slate-800 shadow-sm flex items-center justify-center text-slate-500 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100"
          title="Show chat history"
        >
          <ClipboardList className="w-4 h-4" />
        </button>
      )}
    </div>
  );
}

function Bubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  return (
    <div className={clsx("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={clsx(
          "max-w-[80%] px-4 py-3 rounded-2xl text-sm leading-relaxed",
          isUser
            ? "bg-gradient-to-br from-satori-green to-satori-teal text-white rounded-br-md"
            : "bg-slate-100 dark:bg-slate-800 text-slate-800 dark:text-slate-100 rounded-bl-md border border-slate-200 dark:border-slate-700",
        )}
      >
        {isUser
          ? message.content
          : <span dangerouslySetInnerHTML={{ __html: message.content }} />}
      </div>
    </div>
  );
}

function TypingBubble() {
  return (
    <div className="flex justify-start">
      <div className="bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-2xl rounded-bl-md px-4 py-3 inline-flex items-center gap-1">
        <span className="block w-2 h-2 rounded-full bg-slate-400 dark:bg-slate-500 animate-[typingbounce_1.2s_ease_-0.32s_infinite]" />
        <span className="block w-2 h-2 rounded-full bg-slate-400 dark:bg-slate-500 animate-[typingbounce_1.2s_ease_-0.16s_infinite]" />
        <span className="block w-2 h-2 rounded-full bg-slate-400 dark:bg-slate-500 animate-[typingbounce_1.2s_ease_0s_infinite]" />
      </div>
      <style>{`
        @keyframes typingbounce {
          0%, 60%, 100% { transform: translateY(0); opacity: 0.4; }
          30%           { transform: translateY(-4px); opacity: 1; }
        }
      `}</style>
    </div>
  );
}

function Welcome({ onPrompt }: { onPrompt: (s: string) => void }) {
  return (
    <div className="h-full flex flex-col items-center justify-center text-center max-w-2xl mx-auto pt-8">
      <div className="px-6 py-4 rounded-2xl bg-gradient-to-br from-satori-green/5 to-satori-teal/5 dark:from-satori-green/15 dark:to-satori-teal/15 border border-satori-green/20 dark:border-satori-green/30 flex items-center justify-center mb-6">
        <img src="/static/tmc-logo-black.png" className="h-12 w-auto object-contain dark:hidden" alt="TMC" />
        <img src="/static/tmc-logo-white.png" className="h-12 w-auto object-contain hidden dark:block" alt="TMC" />
      </div>
      <h2 className="font-sans text-3xl font-bold text-slate-900 dark:text-slate-100 mb-4 tracking-tighter2">How can I help you today?</h2>
      <p className="text-sm text-slate-600 dark:text-slate-300 leading-relaxed max-w-xl mb-8">
        I&apos;m connected to your workforce data and can help you analyse attendance patterns, timesheet hours,
        resource allocation, and employee capabilities in real-time.{" "}
        <span className="font-semibold dark:text-slate-100">I&apos;m fully bilingual and can converse and respond in both English and Urdu.</span>
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 w-full max-w-xl">
        {[
          "Who's on the bench right now?",
          "Show me top performers by capability score",
          "Find me a React developer for a new project",
          "How many people were late today?",
        ].map(p => (
          <button
            key={p}
            onClick={() => onPrompt(p)}
            className="text-left px-4 py-3 bg-white dark:bg-satori-paper border border-slate-200 dark:border-slate-800 rounded-lg text-sm text-slate-700 dark:text-slate-200 hover:border-satori-green hover:bg-satori-green/5 dark:hover:bg-satori-green/10 transition shadow-sm"
          >
            {p}
          </button>
        ))}
      </div>
    </div>
  );
}

interface ConvSummary { id: string; title: string; updated_at: string; }
function groupByDate(items: ConvSummary[]) {
  const groups: Record<string, ConvSummary[]> = { Today: [], Yesterday: [], "This Week": [], Older: [] };
  const now = new Date();
  for (const item of items) {
    const d = item.updated_at ? new Date(item.updated_at) : new Date();
    const diff = Math.floor((now.getTime() - d.getTime()) / 86_400_000);
    const key = diff === 0 ? "Today" : diff === 1 ? "Yesterday" : diff < 7 ? "This Week" : "Older";
    groups[key].push(item);
  }
  return groups;
}
