"use client";

import { useSyncExternalStore, type ReactNode } from "react";
import { Button, Dialog } from "./index";

type Pending = {
  title: string;
  body?: ReactNode;
  confirmLabel?: string;
  danger?: boolean;
  resolve: (ok: boolean) => void;
};

let pending: Pending | null = null;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

/** Promise-based confirmation that renders in the app's own dialog style. */
export function confirmAction(opts: Omit<Pending, "resolve">) {
  return new Promise<boolean>((resolve) => {
    pending?.resolve(false);
    pending = { ...opts, resolve };
    emit();
  });
}

function settle(ok: boolean) {
  pending?.resolve(ok);
  pending = null;
  emit();
}

export function ConfirmHost() {
  const p = useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => pending,
    () => null,
  );
  return (
    <Dialog
      open={!!p}
      onOpenChange={(v) => !v && settle(false)}
      title={p?.title ?? ""}
      description={p?.body}
      width={420}
      footer={
        <>
          <Button variant="ghost" onClick={() => settle(false)}>
            Cancel
          </Button>
          <Button variant={p?.danger ? "danger" : "primary"} onClick={() => settle(true)} autoFocus>
            {p?.confirmLabel ?? "Confirm"}
          </Button>
        </>
      }
    />
  );
}
