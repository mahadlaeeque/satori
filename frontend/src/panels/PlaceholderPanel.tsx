import { Construction } from "lucide-react";

export default function PlaceholderPanel({ name }: { name: string }) {
  return (
    <div className="h-full flex items-center justify-center">
      <div className="text-center max-w-md">
        <div className="mx-auto w-14 h-14 rounded-full bg-white dark:bg-satori-paper border border-slate-300 dark:border-slate-700 flex items-center justify-center mb-4">
          <Construction className="w-7 h-7 text-satori-green" />
        </div>
        <h2 className="text-lg font-semibold text-slate-800 dark:text-slate-100 mb-2">{name}</h2>
        <p className="text-sm text-slate-500 dark:text-slate-400 leading-relaxed">
          This panel hasn't been migrated to React yet. The legacy version is still available at
          <a href="/" className="text-satori-green underline mx-1">localhost:8080/</a>
          while we phase the rewrite.
        </p>
      </div>
    </div>
  );
}
