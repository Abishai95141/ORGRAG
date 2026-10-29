"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button, Field, Input } from "@/components/ui";
import { IconArrowRight } from "@/components/icons";
import { api, ApiError } from "@/lib/api";
import { qk } from "@/lib/queries";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const qc = useQueryClient();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await api.login(email, password);
      await qc.invalidateQueries({ queryKey: qk.me });
      router.replace(params.get("next") || "/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not sign in");
      setLoading(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-5">
      <div>
        <h1 className="font-display text-[40px] leading-none tracking-tight">Welcome back</h1>
        <p className="mt-3 text-[14px] text-ink-3">Sign in to pick up where your organizations left off.</p>
      </div>
      <Field label="Email" htmlFor="email">
        <Input id="email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />
      </Field>
      <Field label="Password" htmlFor="password" error={error}>
        <Input id="password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
      </Field>
      <Button type="submit" variant="primary" size="lg" loading={loading} className="justify-center">
        Sign in <IconArrowRight size={16} />
      </Button>
      <p className="text-center text-[13px] text-ink-3">
        New here?{" "}
        <Link href={`/signup${params.get("next") ? `?next=${encodeURIComponent(params.get("next")!)}` : ""}`} className="font-medium text-ink underline decoration-line-strong underline-offset-4 hover:decoration-accent">
          Create an account
        </Link>
      </p>
    </form>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
