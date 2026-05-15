import { useState } from "react";
import { Mic, HelpCircle, X } from "lucide-react";
import VoiceModal from "./VoiceModal";

const HELP_TOPICS = [
  "How do I use the attendance charts?",
  "How do I use the chat assistant?",
  "How do I use voice input?",
  "What is the Capability Matrix?",
  "How do I change settings?",
  "What data is available in Satori?",
];

export default function FabButtons() {
  const [voiceOpen, setVoiceOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);

  return (
    <>
      {/* Floating action buttons — bottom right corner */}
      <div className="fixed bottom-7 right-7 z-40 flex flex-col gap-3 items-end">
        <button
          onClick={() => setHelpOpen(true)}
          title="Help — how to use Satori"
          className="w-14 h-14 rounded-full bg-gradient-to-br from-satori-green to-satori-teal shadow-lg hover:scale-105 transition-transform flex items-center justify-center"
        >
          <HelpCircle className="w-6 h-6 text-white" />
        </button>
        <button
          onClick={() => setVoiceOpen(true)}
          title="Talk to Satori (voice)"
          className="w-14 h-14 rounded-full bg-gradient-to-br from-satori-green to-satori-teal shadow-lg hover:scale-105 transition-transform flex items-center justify-center"
        >
          <Mic className="w-6 h-6 text-white" />
        </button>
      </div>

      {helpOpen && (
        <div className="fixed bottom-28 right-7 z-40 w-[360px] max-h-[500px] card flex flex-col overflow-hidden shadow-2xl animate-[slideup_0.25s_ease]">
          <div className="px-4 py-3 bg-gradient-to-br from-satori-green to-satori-teal text-white flex items-center justify-between">
            <div className="text-sm font-semibold">Need a hand?</div>
            <button onClick={() => setHelpOpen(false)} className="text-white/80 hover:text-white">
              <X className="w-4 h-4" />
            </button>
          </div>
          <div className="p-3 overflow-y-auto space-y-1.5">
            {HELP_TOPICS.map(t => (
              <button
                key={t}
                onClick={() => askHelp(t)}
                className="block w-full text-left text-xs px-3 py-2 rounded-md bg-slate-50 dark:bg-slate-800 hover:bg-satori-green/10 dark:hover:bg-satori-green/15 hover:text-satori-green dark:hover:text-satori-green transition text-slate-700 dark:text-slate-200"
              >
                {t}
              </button>
            ))}
          </div>
        </div>
      )}

      <VoiceModal open={voiceOpen} onClose={() => setVoiceOpen(false)} />
    </>
  );
}

async function askHelp(question: string) {
  try {
    const r = await fetch("/api/help", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    const data = await r.json();
    alert(typeof data?.answer === "string" ? data.answer.replace(/<[^>]+>/g, "") : "No response.");
  } catch (e: any) {
    alert("Failed: " + (e?.message ?? "unknown"));
  }
}
