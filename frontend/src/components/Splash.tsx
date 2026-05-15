import { useEffect, useState } from "react";

export default function Splash() {
  const [visible, setVisible] = useState(true);
  const [fading, setFading]   = useState(false);

  useEffect(() => {
    const t1 = window.setTimeout(() => setFading(true),  2400);
    const t2 = window.setTimeout(() => setVisible(false), 3200);
    return () => { clearTimeout(t1); clearTimeout(t2); };
  }, []);

  if (!visible) return null;

  return (
    <div
      className={
        "fixed inset-0 z-[100] flex flex-col items-center justify-center bg-[#0b1220] transition-opacity duration-700 " +
        (fading ? "opacity-0" : "opacity-100")
      }
      style={{
        backgroundImage:
          "radial-gradient(ellipse at center, rgba(125,194,67,0.07) 0%, transparent 60%), " +
          "linear-gradient(rgba(125,194,67,0.04) 1px, transparent 1px), " +
          "linear-gradient(90deg, rgba(125,194,67,0.04) 1px, transparent 1px)",
        backgroundSize: "100% 100%, 64px 64px, 64px 64px",
      }}
    >
      <div className="text-satori-green text-[10px] tracking-[0.4em] font-medium mb-8 animate-[fadein_0.8s_ease_0.3s_both]">
        WELCOME&nbsp;&nbsp;TO&nbsp;&nbsp;THE&nbsp;&nbsp;FUTURE
      </div>

      {/* Compact 200x200 ring with logo centred inside */}
      <div className="relative w-[200px] h-[200px] flex items-center justify-center">
        <div className="absolute inset-0 rounded-full border border-satori-green/25" />
        <div
          className="absolute w-2 h-2 rounded-full bg-satori-green shadow-[0_0_10px_2px_rgba(125,194,67,0.7)]"
          style={{
            top: -4,
            left: "50%",
            marginLeft: -4,
            transformOrigin: "4px 104px",
            animation: "spin 12s linear infinite",
          }}
        />
        <img
          src="/static/tmc-logo-white.png"
          alt="TMC"
          className="relative h-[62px] w-auto object-contain animate-[fadein_0.9s_ease_0.1s_both]"
        />
      </div>

      <div className="mt-8 text-[44px] font-extrabold text-white tracking-tighter2 leading-none animate-[slideup_0.7s_ease_0.6s_both]">
        satori
      </div>
      <div className="mt-2.5 h-[2px] w-20 bg-satori-green animate-[grow_0.8s_ease_0.9s_both]" />
      <div className="mt-3 text-xs text-slate-400 tracking-wide animate-[slideup_0.7s_ease_1.1s_both]">
        TMC&apos;s Capability Intelligence Matrix
      </div>

      <style>{`
        @keyframes fadein  { from { opacity: 0; transform: scale(0.94); } to { opacity: 1; transform: scale(1); } }
        @keyframes slideup { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        @keyframes grow    { from { width: 0; } to { width: 5rem; } }
        @keyframes spin    { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
}
