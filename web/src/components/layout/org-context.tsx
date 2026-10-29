"use client";

import { createContext, useContext } from "react";
import type { OrgSummary, User } from "@/lib/types";

type Ctx = { org: OrgSummary; orgs: OrgSummary[]; user: User };

export const OrgContext = createContext<Ctx | null>(null);

export function useCurrentOrg() {
  const ctx = useContext(OrgContext);
  if (!ctx) throw new Error("useCurrentOrg must be used inside an org layout");
  return ctx;
}
