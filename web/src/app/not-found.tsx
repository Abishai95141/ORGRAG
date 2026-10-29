import Link from "next/link";
import { NoResultsArt } from "@/components/illustrations";

export default function NotFound() {
  return (
    <main className="grid min-h-screen place-items-center px-6 text-center">
      <div className="flex flex-col items-center">
        <NoResultsArt className="mb-6 max-w-[200px]" />
        <h1 className="font-display text-[34px] tracking-tight">Nothing at this depth</h1>
        <p className="mt-2 text-[14px] text-ink-3">The page you were looking for isn&apos;t here.</p>
        <Link href="/" className="mt-6 text-[14px] font-medium underline decoration-line-strong underline-offset-4 hover:decoration-accent">
          Back to Strata
        </Link>
      </div>
    </main>
  );
}
