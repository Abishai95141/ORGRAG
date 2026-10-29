"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button, Field, Input } from "@/components/ui";
import { IconArrowRight } from "@/components/icons";
import { api, ApiError } from "@/lib/api";
import { qk } from "@/lib/queries";

function SignupForm() {
  const router = useRouter();
  const params = useSearchParams();
  const qc = useQueryClient();
  const [form, setForm] = useState({ name: "", email: "", password: "" });
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (form.password.length < 8) return setError("Use at least 8 characters.");
    setLoading(true);
    setError(null);
    try {
      await api.signup(form.name, form.email, form.password);
      await qc.invalidateQueries({ queryKey: qk.me });
      router.replace(params.get("next") || "/onboarding");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create account");
      setLoading(false);
    }
  }

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });

  return (
    <form onSubmit={submit} className="flex flex-col gap-5">
      <div>
        <h1 className="font-display text-[40px] leading-none tracking-tight">Start digging</h1>
        <p className="mt-3 text-[14px] text-ink-3">Create an account, then bring your first organization&apos;s knowledge in.</p>
      </div>
      <Field label="Full name" htmlFor="name">
        <Input id="name" autoComplete="name" required value={form.name} onChange={set("name")} autoFocus />
      </Field>
      <Field label="Work email" htmlFor="email">
        <Input id="email" type="email" autoComplete="email" required value={form.email} onChange={set("email")} />
      </Field>
      <Field label="Password" htmlFor="password" hint="At least 8 characters." error={error}>
        <Input id="password" type="password" autoComplete="new-password" required value={form.password} onChange={set("password")} />
      </Field>
      <Button type="submit" variant="primary" size="lg" loading={loading} className="justify-center">
        Create account <IconArrowRight size={16} />
      </Button>
      <p className="text-center text-[13px] text-ink-3">
        Already have an account?{" "}
        <Link href="/login" className="font-medium text-ink underline decoration-line-strong underline-offset-4 hover:decoration-accent">
          Sign in
        </Link>
      </p>
    </form>
  );
}

export default function SignupPage() {
  return (
    <Suspense>
      <SignupForm />
    </Suspense>
  );
}
