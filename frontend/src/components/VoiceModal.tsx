/**
 * Live voice agent — Browser ←→ Gemini Multimodal Live API via WebSocket.
 * Tool calls (BigQuery SQL) are routed through /api/voice/query on the backend.
 */
import { useEffect, useRef, useState } from "react";
import { Mic, X, Loader2 } from "lucide-react";
import { api } from "@/api/client";
import clsx from "clsx";

const GEMINI_WS_BASE =
  "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent";

type VoiceState = "connecting" | "listening" | "speaking" | "closing";

interface VoiceSessionConfig {
  apiKey: string;
  model: string;
  voice: string;
  systemInstruction: string;
  tools: any[];
}

export default function VoiceModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [state, setState] = useState<VoiceState>("connecting");
  const [statusText, setStatusText] = useState("Connecting to Satori…");

  const wsRef = useRef<WebSocket | null>(null);
  const captureCtxRef = useRef<AudioContext | null>(null);
  const playCtxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const nextPlayTimeRef = useRef(0);
  const isSpeakingRef = useRef(false);
  const setupDoneRef = useRef(false);
  const setupTimeoutRef = useRef<number | null>(null);
  const closingRef = useRef(false);

  useEffect(() => {
    if (!open) return;
    start();
    return () => stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  async function start() {
    closingRef.current = false;
    setupDoneRef.current = false;
    setState("connecting");
    setStatusText("Connecting to Satori…");

    // 1. Fetch session config from backend
    let config: VoiceSessionConfig;
    try {
      config = await api.post<VoiceSessionConfig>("/api/voice/session");
    } catch (e: any) {
      setStatusText(e?.message ?? "Failed to get session config.");
      window.setTimeout(stop, 3000);
      return;
    }

    // 2. Open mic
    try {
      streamRef.current = await navigator.mediaDevices.getUserMedia({ audio: true });
      captureCtxRef.current = new (window.AudioContext || (window as any).webkitAudioContext)();
      playCtxRef.current    = new (window.AudioContext || (window as any).webkitAudioContext)({ sampleRate: 24000 });
      sourceRef.current     = captureCtxRef.current.createMediaStreamSource(streamRef.current);
      processorRef.current  = captureCtxRef.current.createScriptProcessor(4096, 1, 1);
      sourceRef.current.connect(processorRef.current);
      processorRef.current.connect(captureCtxRef.current.destination);
    } catch (e: any) {
      setStatusText("Microphone permission denied.");
      window.setTimeout(stop, 3000);
      return;
    }

    // 3. WebSocket to Gemini Live
    const ws = new WebSocket(`${GEMINI_WS_BASE}?key=${config.apiKey}`);
    wsRef.current = ws;

    ws.onopen = () => {
      const setupMsg = {
        setup: {
          model: config.model,
          generationConfig: {
            responseModalities: ["AUDIO"],
            speechConfig: { voiceConfig: { prebuiltVoiceConfig: { voiceName: config.voice } } },
          },
          systemInstruction: { parts: [{ text: config.systemInstruction }] },
          tools: config.tools,
        },
      };
      ws.send(JSON.stringify(setupMsg));
      setupTimeoutRef.current = window.setTimeout(() => {
        if (!setupDoneRef.current) {
          setStatusText("Setup timed out.");
          stop();
        }
      }, 8000);
    };

    ws.onmessage = async (evt) => {
      let data: any;
      try {
        data = JSON.parse(typeof evt.data === "string" ? evt.data : await evt.data.text());
      } catch {
        return;
      }

      if (data.setupComplete) {
        setupDoneRef.current = true;
        if (setupTimeoutRef.current) window.clearTimeout(setupTimeoutRef.current);
        setState("listening");
        setStatusText("Listening… speak now");
        return;
      }

      // Tool calls — BigQuery SQL via /api/voice/query
      if (data.toolCall?.functionCalls?.length) {
        const responses = [];
        for (const fc of data.toolCall.functionCalls) {
          try {
            const r = await api.post<{ result: string }>("/api/voice/query", { sql: fc.args.sql });
            responses.push({ id: fc.id, name: fc.name, response: { output: r.result } });
          } catch (e: any) {
            responses.push({ id: fc.id, name: fc.name, response: { output: "Query failed: " + e.message } });
          }
        }
        ws.send(JSON.stringify({ toolResponse: { functionResponses: responses } }));
        return;
      }

      // Audio output
      if (data.serverContent) {
        const sc = data.serverContent;
        if (sc.modelTurn?.parts) {
          for (const part of sc.modelTurn.parts) {
            if (part.inlineData?.data) {
              if (!isSpeakingRef.current) {
                isSpeakingRef.current = true;
                setState("speaking");
                setStatusText("Satori is speaking…");
              }
              playPcm(part.inlineData.data);
            }
          }
        }
        if (sc.turnComplete) {
          isSpeakingRef.current = false;
          setState("listening");
          setStatusText("Listening… speak now");
        }
        if (sc.interrupted) {
          isSpeakingRef.current = false;
          nextPlayTimeRef.current = 0;
        }
      }
    };

    ws.onerror = (ev) => {
      console.error("[VoiceModal] WebSocket error", ev);
      if (!closingRef.current) {
        setStatusText("Connection error — check console for details.");
      }
    };
    ws.onclose = (ev) => {
      console.warn(`[VoiceModal] WebSocket closed — code=${ev.code} reason="${ev.reason}" wasClean=${ev.wasClean}`);
      if (!closingRef.current) {
        // Common codes:
        //   1000 normal · 1006 abnormal · 1008 policy violation · 1011 server error
        //   4xxx Gemini-specific (usually auth / model unavailable)
        const reasonText = ev.reason || (
          ev.code === 1008 ? "policy violation (API key or model issue)" :
          ev.code === 1011 ? "server error" :
          ev.code === 1006 ? "abnormal close (network or auth)" :
          ev.code >= 4000  ? "auth or model error" :
          "unknown"
        );
        setStatusText(`Disconnected (${ev.code}: ${reasonText})`);
        window.setTimeout(stop, 4000);  // give user time to read the reason
      }
    };

    // 4. Mic → PCM16 → WebSocket
    const captureSR = captureCtxRef.current!.sampleRate;
    processorRef.current!.onaudioprocess = (e) => {
      if (!ws || ws.readyState !== WebSocket.OPEN || !setupDoneRef.current) return;
      const input = e.inputBuffer.getChannelData(0);
      const pcm16 = new Int16Array(input.length);
      for (let i = 0; i < input.length; i++) {
        pcm16[i] = Math.max(-32768, Math.min(32767, Math.round(input[i] * 32767)));
      }
      let samples = pcm16;
      if (captureSR !== 16000) {
        const ratio = captureSR / 16000;
        const outLen = Math.floor(pcm16.length / ratio);
        samples = new Int16Array(outLen);
        for (let i = 0; i < outLen; i++) samples[i] = pcm16[Math.round(i * ratio)];
      }
      const bytes = new Uint8Array(samples.buffer);
      let b64 = "";
      for (let i = 0; i < bytes.length; i++) b64 += String.fromCharCode(bytes[i]);
      // New Gemini Live API audio format (mediaChunks was deprecated).
      ws.send(JSON.stringify({
        realtimeInput: {
          audio: { mimeType: "audio/pcm;rate=16000", data: btoa(b64) },
        },
      }));
    };
  }

  function playPcm(b64data: string) {
    const ctx = playCtxRef.current;
    if (!ctx) return;
    try {
      const raw = atob(b64data);
      const bytes = new Uint8Array(raw.length);
      for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
      const pcm16 = new Int16Array(bytes.buffer);
      const floats = new Float32Array(pcm16.length);
      for (let i = 0; i < pcm16.length; i++) floats[i] = pcm16[i] / 32768.0;
      const buf = ctx.createBuffer(1, floats.length, 24000);
      buf.copyToChannel(floats, 0);
      const src = ctx.createBufferSource();
      src.buffer = buf;
      src.connect(ctx.destination);
      const when = Math.max(ctx.currentTime, nextPlayTimeRef.current);
      src.start(when);
      nextPlayTimeRef.current = when + buf.duration;
    } catch { /* ignore */ }
  }

  function stop() {
    closingRef.current = true;
    setState("closing");
    setupDoneRef.current = false;
    try { processorRef.current?.disconnect(); } catch {}
    try { sourceRef.current?.disconnect(); } catch {}
    streamRef.current?.getTracks().forEach(t => t.stop());
    try { captureCtxRef.current?.close(); } catch {}
    try { playCtxRef.current?.close(); } catch {}
    try { if (wsRef.current?.readyState === WebSocket.OPEN) wsRef.current.close(); } catch {}
    processorRef.current = null;
    sourceRef.current    = null;
    streamRef.current    = null;
    captureCtxRef.current = null;
    playCtxRef.current   = null;
    wsRef.current        = null;
    nextPlayTimeRef.current = 0;
    if (setupTimeoutRef.current) window.clearTimeout(setupTimeoutRef.current);
    onClose();
  }

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center" onClick={stop}>
      <div className="text-center" onClick={(e) => e.stopPropagation()}>
        <div className={clsx(
          "relative mx-auto mb-6 w-32 h-32 rounded-full flex items-center justify-center transition-all",
          state === "speaking" ? "bg-gradient-to-br from-satori-green to-satori-teal animate-pulse" :
          state === "listening" ? "bg-satori-green/20 border-4 border-satori-green" :
          "bg-slate-800 border-4 border-slate-600",
        )}>
          {state === "connecting" || state === "closing"
            ? <Loader2 className="w-12 h-12 text-slate-300 animate-spin" />
            : <Mic className={clsx("w-12 h-12", state === "speaking" ? "text-white" : "text-satori-green")} />}
          {state === "listening" && (
            <span className="absolute inset-0 rounded-full border-4 border-satori-green/30 animate-ping" />
          )}
        </div>
        <div className="text-slate-200 text-sm max-w-md">{statusText}</div>
        <button
          onClick={stop}
          className="mt-6 inline-flex items-center gap-2 px-5 py-2 rounded-full bg-red-500/20 border border-red-500/40 text-red-300 text-sm hover:bg-red-500/30 transition"
        >
          <X className="w-4 h-4" /> End call
        </button>
      </div>
    </div>
  );
}
