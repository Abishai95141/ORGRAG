"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { useCurrentOrg } from "@/components/layout/org-context";
import { Button, Dialog, Field, Input, Textarea } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { qk } from "@/lib/queries";
import type { DocRequest } from "@/lib/types";

/**
 * Ask the org's admins for a document the knowledge base lacks: from an answer that
 * couldn't answer (`messageId`: the API takes the question from it), or from a
 * knowledge gap (`question`). Asking again for the same thing returns the open request.
 */
export function RequestDocumentDialog({
  open,
  onOpenChange,
  messageId,
  question,
  onDone,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  messageId?: string;
  /** The question that went unanswered, shown for context. */
  question?: string;
  onDone?: (r: DocRequest) => void;
}) {
  const { org } = useCurrentOrg();
  const qc = useQueryClient();
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    setTitle("");
    setNote("");
    setError(null);
  }, [open]);

  async function send() {
    if (!title.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const req = await api.requestDocument(org.slug, {
        title: title.trim(),
        note: note.trim() || undefined,
        question: messageId ? undefined : question,
        message_id: messageId,
      });
      await Promise.all([
        qc.invalidateQueries({ queryKey: qk.docRequests(org.slug) }),
        qc.invalidateQueries({ queryKey: qk.gaps(org.slug) }),
      ]);
      toast("Request sent to your admins", {
        description: "You'll see on Requests when a document answers it.",
        action: { label: "View", onClick: () => router.push(`/o/${org.slug}/requests`) },
      });
      onDone?.(req);
      onOpenChange(false);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't send the request.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      width={540}
      title="Request a document"
      description="Tell your admins what's missing from the knowledge base. When they add a document that answers it, you'll find it under Requests."
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" loading={saving} disabled={!title.trim()} onClick={send}>
            Send request
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {question ? (
          <div className="rounded-[10px] border border-line bg-surface-2/60 px-3 py-2 text-[13px]">
            <div className="font-mono text-[10.5px] tracking-wider text-ink-3 uppercase">Unanswered question</div>
            <p className="mt-0.5 line-clamp-3 text-ink-2">{question}</p>
          </div>
        ) : null}
        <Field label="What document is missing?" htmlFor="dr-title" hint="A title or a short description is enough.">
          <Input
            id="dr-title"
            value={title}
            maxLength={300}
            autoFocus
            placeholder="e.g. The parental leave policy"
            onChange={(e) => setTitle(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && send()}
          />
        </Field>
        <Field label="Note" htmlFor="dr-note" hint="Optional: who owns it, where it might live, why you need it.">
          <Textarea id="dr-note" rows={3} maxLength={2000} value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
        {error ? (
          <p role="alert" className="rounded-[10px] border border-err/30 bg-err-soft px-3 py-2 text-[13px] text-err">
            {error}
          </p>
        ) : null}
      </div>
    </Dialog>
  );
}
