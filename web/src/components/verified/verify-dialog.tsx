"use client";

import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { useCurrentOrg } from "@/components/layout/org-context";
import { Button, Dialog, Field, Input, Select, Textarea } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { qk } from "@/lib/queries";
import type { VerifiedAnswer } from "@/lib/types";

const EXPIRY = [
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
  { value: "180", label: "6 months" },
  { value: "365", label: "1 year" },
  { value: "never", label: "Never" },
] as const;
type Expiry = (typeof EXPIRY)[number]["value"];

/**
 * Promote a chat answer to a verified answer (`messageId`), or edit an existing one
 * (`existing`). The answer is indexed into the chosen collection, so whoever can read
 * that collection finds it — the API refuses a collection that would show it to
 * people who can't read its sources.
 */
export function VerifyDialog({
  open,
  onOpenChange,
  messageId,
  question: initialQuestion,
  answer: initialAnswer,
  sourceCollections = [],
  existing,
  onDone,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  messageId?: string;
  question?: string;
  answer?: string;
  /** Names of the collections the answer's sources came from: the first is preselected. */
  sourceCollections?: string[];
  existing?: VerifiedAnswer;
  onDone?: (v: VerifiedAnswer) => void;
}) {
  const { org, user } = useCurrentOrg();
  const qc = useQueryClient();
  const collections = useQuery({ queryKey: qk.collections(org.slug), queryFn: () => api.collections(org.slug), enabled: open });
  const members = useQuery({ queryKey: qk.members(org.slug), queryFn: () => api.members(org.slug), enabled: open });
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [collectionId, setCollectionId] = useState<string | undefined>();
  const [ownerId, setOwnerId] = useState<string>(user.id);
  const [expiry, setExpiry] = useState<Expiry>("90");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    setQuestion(existing?.question ?? initialQuestion ?? "");
    setAnswer(existing?.answer ?? initialAnswer ?? "");
    setOwnerId(existing?.owner?.id ?? user.id);
    setExpiry("90");
    setCollectionId(existing?.collection_id);
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (!open || collectionId || !collections.data?.length) return;
    const byName = sourceCollections.map((n) => collections.data.find((c) => c.name === n)).find(Boolean);
    setCollectionId(byName?.id ?? collections.data[0].id);
  }, [open, collections.data, collectionId, sourceCollections]);

  async function save() {
    if (!collectionId || !question.trim() || !answer.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const days = expiry === "never" ? null : Number(expiry);
      const saved = existing
        ? await api.updateVerified(org.slug, existing.id, {
            question: question.trim(),
            answer: answer.trim(),
            collection_id: collectionId,
            owner_id: ownerId,
          })
        : await api.createVerified(org.slug, {
            message_id: messageId,
            question: question.trim(),
            answer: answer.trim(),
            collection_id: collectionId,
            owner_id: ownerId,
            expires_in_days: days,
          });
      await qc.invalidateQueries({ queryKey: qk.verified(org.slug) });
      toast(existing ? "Verified answer updated" : "Answer verified — it will be found and cited first");
      onDone?.(saved);
      onOpenChange(false);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't save the verified answer.");
    } finally {
      setSaving(false);
    }
  }

  const collectionOptions = (collections.data ?? []).map((c) => ({
    value: c.id,
    label: c.name,
    hint: c.visibility === "restricted" ? "Restricted" : "Everyone",
  }));
  const ownerOptions = (members.data ?? []).map((m) => ({ value: m.user.id, label: m.user.name || m.user.email }));

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      width={620}
      title={existing ? "Edit verified answer" : "Verify this answer"}
      description={
        existing
          ? "Editing keeps the current verification. Use Re-verify to vouch for it again."
          : "A verified answer is found and cited ahead of documents, with your name on it, until it expires."
      }
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" loading={saving} disabled={!collectionId || !question.trim() || !answer.trim()} onClick={save}>
            {existing ? "Save" : "Verify answer"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label="Question" htmlFor="va-question" hint="Phrase it the way people ask it.">
          <Input id="va-question" value={question} maxLength={2000} onChange={(e) => setQuestion(e.target.value)} />
        </Field>
        <Field label="Answer" htmlFor="va-answer" hint="Markdown. Citation numbers are removed when it's indexed.">
          <Textarea id="va-answer" rows={9} value={answer} onChange={(e) => setAnswer(e.target.value)} />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Collection" htmlFor="va-collection" hint="Who can read it decides who sees this answer.">
            <Select id="va-collection" value={collectionId} onValueChange={setCollectionId} options={collectionOptions} placeholder="Choose a collection" />
          </Field>
          <Field label="Owner" htmlFor="va-owner" hint="Who keeps it up to date.">
            <Select id="va-owner" value={ownerId} onValueChange={setOwnerId} options={ownerOptions} />
          </Field>
        </div>
        {existing ? null : (
          <Field label="Valid for" htmlFor="va-expiry" hint="After this it leaves search until someone re-verifies it.">
            <Select id="va-expiry" value={expiry} onValueChange={(v) => setExpiry(v as Expiry)} options={EXPIRY.map((e) => ({ ...e }))} />
          </Field>
        )}
        {error ? (
          <p role="alert" className="rounded-[10px] border border-err/30 bg-err-soft px-3 py-2 text-[13px] text-err">
            {error}
          </p>
        ) : null}
      </div>
    </Dialog>
  );
}
