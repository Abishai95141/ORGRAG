import { AuthArt, Wordmark } from "@/components/illustrations";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <main className="grid min-h-screen lg:grid-cols-[minmax(420px,1fr)_1.1fr]">
      <section className="flex flex-col px-6 py-8 sm:px-12">
        <Wordmark />
        <div className="flex flex-1 items-center">
          <div className="mx-auto w-full max-w-[360px] animate-rise">{children}</div>
        </div>
        <p className="text-[12px] text-ink-3">Every organization. One answer. Sources you can check.</p>
      </section>
      <aside className="paper-grain relative hidden flex-col overflow-hidden border-l border-line bg-surface-2/50 lg:flex">
        <div className="relative min-h-0 flex-1">
          <AuthArt className="absolute inset-0 text-ink-3" />
        </div>
        <div className="relative px-10 pt-2 pb-10">
          <p className="max-w-md font-display text-[30px] leading-[1.15] tracking-tight">
            Ask once. Strata searches each organization you belong to, reads what matters, and{" "}
            <em className="text-accent">cites every line</em>.
          </p>
        </div>
      </aside>
    </main>
  );
}
