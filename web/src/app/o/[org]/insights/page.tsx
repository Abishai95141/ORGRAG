"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { SourceKindIcon } from "@/components/chat/source-kind";
import { IconThumbDown, IconThumbUp } from "@/components/icons";
import { useCurrentOrg } from "@/components/layout/org-context";
import { Card, PageHeader, Segmented, Skeleton, Stat } from "@/components/ui";
import { api } from "@/lib/api";
import type { Insights } from "@/lib/types";
import { qk } from "@/lib/queries";
import { formatNumber, relativeTime } from "@/lib/utils";

export default function InsightsPage() {
  const { org } = useCurrentOrg();
  const [days, setDays] = useState<"7" | "30" | "90">("30");
  const insights = useQuery({ queryKey: qk.insights(org.slug, Number(days)), queryFn: () => api.insights(org.slug, Number(days)) });
  const d = insights.data;
  const fbTotal = d ? d.feedback.up + d.feedback.down : 0;

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl px-5 py-10 sm:px-8">
        <PageHeader
          eyebrow={org.name}
          title="Insights"
          description="What people ask, which sources answer them, and whether the answers helped."
          actions={
            <Segmented
              size="sm"
              value={days}
              onChange={setDays}
              options={[
                { value: "7", label: "7 days" },
                { value: "30", label: "30 days" },
                { value: "90", label: "90 days" },
              ]}
            />
          }
        />

        {!d ? (
          <div className="flex flex-col gap-4">
            <Skeleton className="h-24 rounded-[14px]" />
            <Skeleton className="h-64 rounded-[14px]" />
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            <Card className="grid grid-cols-2 gap-6 p-5 sm:grid-cols-5">
              <Stat label="Questions" value={formatNumber(d.totals.questions)} sub={`last ${days} days`} />
              <Stat label="Conversations" value={formatNumber(d.totals.conversations)} />
              <Stat label="Active people" value={formatNumber(d.totals.active_users)} />
              <Stat label="Documents" value={formatNumber(d.totals.documents)} />
              <Stat label="Chunks" value={formatNumber(d.totals.chunks)} />
            </Card>

            <Card className="p-5">
              <QuestionsChart data={d.questions_per_day} />
            </Card>

            <div className="grid gap-4 md:grid-cols-[1.4fr_1fr]">
              <Card className="p-5">
                <h2 className="text-[14px] font-semibold">Most-cited sources</h2>
                <p className="mt-0.5 text-[12.5px] text-ink-3">Citations in answers, last {days} days</p>
                <TopSources items={d.top_sources} />
              </Card>
              <Card className="flex flex-col p-5">
                <h2 className="text-[14px] font-semibold">Answer feedback</h2>
                <p className="mt-0.5 text-[12.5px] text-ink-3">{fbTotal ? `${fbTotal} ratings` : "No ratings yet"}</p>
                <div className="mt-5 flex items-end gap-8">
                  <div>
                    <div className="text-[44px] leading-none font-semibold tracking-tight tabular-nums">
                      {fbTotal ? Math.round((d.feedback.up / fbTotal) * 100) : "—"}
                      {fbTotal ? <span className="text-[22px] text-ink-3">%</span> : null}
                    </div>
                    <div className="mt-1.5 text-[12px] text-ink-3">rated helpful</div>
                  </div>
                  <div className="flex flex-col gap-1.5 pb-1 text-[13px]">
                    <span className="inline-flex items-center gap-1.5 text-ink-2">
                      <IconThumbUp size={14} className="text-ok" /> {d.feedback.up} helpful
                    </span>
                    <span className="inline-flex items-center gap-1.5 text-ink-2">
                      <IconThumbDown size={14} className="text-err" /> {d.feedback.down} not helpful
                    </span>
                  </div>
                </div>
              </Card>
            </div>

            <Card className="p-5">
              <h2 className="text-[14px] font-semibold">Model usage</h2>
              <p className="mt-0.5 text-[12.5px] text-ink-3">Tokens spent answering questions in this organization, last {days} days</p>
              <ModelUsage items={d.usage ?? []} />
            </Card>

            <Card className="p-5">
              <h2 className="text-[14px] font-semibold">Recent questions</h2>
              {d.recent_questions.length ? (
                <ul className="mt-3 divide-y divide-line">
                  {d.recent_questions.map((q, i) => (
                    <li key={i} className="flex items-baseline gap-3 py-2.5 text-[13px]">
                      <span className="min-w-0 flex-1 truncate text-ink">{q.query}</span>
                      <span className="shrink-0 text-ink-3">{q.user_name}</span>
                      <span className="w-16 shrink-0 text-right font-mono text-[11px] text-ink-3">{q.hits} hits</span>
                      <span className="w-20 shrink-0 text-right text-[12px] text-ink-3">{relativeTime(q.created_at)}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-3 text-[13px] text-ink-3">Nobody has asked anything yet.</p>
              )}
            </Card>
          </div>
        )}
      </div>
    </div>
  );
}

/** Single-series column chart: questions per day, with hover tooltip and a table view. */
function QuestionsChart({ data }: { data: { date: string; count: number }[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const [table, setTable] = useState(false);
  const W = 880;
  const H = 200;
  const pad = { l: 32, r: 8, t: 12, b: 24 };
  const max = Math.max(1, ...data.map((d) => d.count));
  const ticks = useMemo(() => niceTicks(max), [max]);
  const top = ticks.at(-1)!;
  const band = (W - pad.l - pad.r) / Math.max(1, data.length);
  const barW = Math.min(24, Math.max(3, band - 2));
  const y = (v: number) => pad.t + (H - pad.t - pad.b) * (1 - v / top);
  const total = data.reduce((a, d) => a + d.count, 0);
  const fmt = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString("en", { month: "short", day: "numeric" });
  const labelEvery = Math.ceil(data.length / 8);

  return (
    <div>
      <div className="mb-3 flex items-start justify-between">
        <div>
          <h2 className="text-[14px] font-semibold">Questions per day</h2>
          <p className="mt-0.5 text-[12.5px] text-ink-3">{total} questions asked</p>
        </div>
        <button onClick={() => setTable((v) => !v)} className="text-[12px] text-ink-3 underline decoration-line-strong underline-offset-4 hover:text-ink">
          {table ? "Show chart" : "Show table"}
        </button>
      </div>
      {table ? (
        <div className="max-h-64 overflow-y-auto">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="border-b border-line text-left font-mono text-[10.5px] tracking-wider text-ink-3 uppercase">
                <th className="py-2 font-normal">Date</th>
                <th className="py-2 text-right font-normal">Questions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {data.map((d) => (
                <tr key={d.date}>
                  <td className="py-1.5 text-ink-2">{fmt(d.date)}</td>
                  <td className="py-1.5 text-right tabular-nums">{d.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="relative">
          <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label={`Questions per day, ${total} total`}>
            {ticks.map((t) => (
              <g key={t}>
                <line x1={pad.l} x2={W - pad.r} y1={y(t)} y2={y(t)} stroke="var(--line)" strokeWidth="1" />
                <text x={pad.l - 8} y={y(t)} dy="0.32em" textAnchor="end" fontSize="10" fontFamily="var(--font-mono)" fill="var(--ink-3)">
                  {formatNumber(t)}
                </text>
              </g>
            ))}
            {data.map((d, i) => {
              const cx = pad.l + band * i + band / 2;
              const h = Math.max(0, y(0) - y(d.count));
              const r = Math.min(4, h, barW / 2);
              const x0 = cx - barW / 2;
              const yTop = y(0) - h;
              return (
                <g key={d.date}>
                  {h > 0 ? (
                    <path
                      d={`M${x0} ${y(0)} V${yTop + r} Q${x0} ${yTop} ${x0 + r} ${yTop} H${x0 + barW - r} Q${x0 + barW} ${yTop} ${x0 + barW} ${yTop + r} V${y(0)} Z`}
                      fill="var(--chart)"
                      opacity={hover === null || hover === i ? 1 : 0.45}
                    />
                  ) : null}
                  {i % labelEvery === 0 ? (
                    <text x={cx} y={H - 6} textAnchor="middle" fontSize="10" fontFamily="var(--font-mono)" fill="var(--ink-3)">
                      {fmt(d.date)}
                    </text>
                  ) : null}
                  {/* hit target: the whole band, not just the bar */}
                  <rect x={pad.l + band * i} y={pad.t} width={band} height={H - pad.t - pad.b} fill="transparent" onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} />
                </g>
              );
            })}
          </svg>
          {hover !== null && data[hover] ? (
            <div
              className="pointer-events-none absolute top-0 -translate-x-1/2 rounded-[8px] border border-line bg-surface px-2.5 py-1.5 text-[12px] shadow-pop"
              style={{ left: `${((pad.l + band * hover + band / 2) / W) * 100}%` }}
            >
              <div className="text-ink-3">{fmt(data[hover].date)}</div>
              <div className="font-semibold tabular-nums">
                {data[hover].count} question{data[hover].count === 1 ? "" : "s"}
              </div>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}

function TopSources({ items }: { items: Insights["top_sources"] }) {
  if (!items.length) return <p className="mt-4 text-[13px] text-ink-3">No citations yet.</p>;
  const max = Math.max(...items.map((i) => i.citations));
  return (
    <ul className="mt-4 flex flex-col gap-3">
      {items.map((s) => (
        <li key={s.source_id} title={`${s.name}: ${s.citations} citations`}>
          <div className="mb-1 flex items-center gap-2 text-[13px]">
            <SourceKindIcon kind={s.kind} size={13} className="text-ink-3" />
            <span className="min-w-0 flex-1 truncate">{s.name}</span>
            <span className="font-mono text-[11.5px] text-ink-2 tabular-nums">{s.citations}</span>
          </div>
          <div className="h-1.5 rounded-full bg-surface-2">
            <div className="h-full rounded-full bg-chart" style={{ width: `${(s.citations / max) * 100}%` }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

function ModelUsage({ items }: { items: Insights["usage"] }) {
  if (!items.length) return <p className="mt-3 text-[13px] text-ink-3">No answers with usage recorded yet.</p>;
  return (
    <div className="mt-3 overflow-x-auto">
      <table className="w-full text-[13px]">
        <thead>
          <tr className="text-left text-[11.5px] text-ink-3">
            <th className="py-1.5 font-medium">Model</th>
            <th className="py-1.5 text-right font-medium">Answers</th>
            <th className="py-1.5 text-right font-medium">Model calls</th>
            <th className="py-1.5 text-right font-medium">Input tokens</th>
            <th className="py-1.5 text-right font-medium">Output tokens</th>
            <th className="py-1.5 text-right font-medium">Cost</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {items.map((u) => (
            <tr key={u.model}>
              <td className="py-2 font-mono text-[12px]">{u.model}</td>
              <td className="py-2 text-right tabular-nums">{formatNumber(u.answers)}</td>
              <td className="py-2 text-right tabular-nums">{formatNumber(u.calls)}</td>
              <td className="py-2 text-right tabular-nums">{formatNumber(u.input_tokens)}</td>
              <td className="py-2 text-right tabular-nums">{formatNumber(u.output_tokens)}</td>
              <td className="py-2 text-right tabular-nums" title={u.cost_usd == null ? "No per-token price for this model" : undefined}>
                {u.cost_usd == null ? "—" : formatCost(u.cost_usd)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatCost(usd: number) {
  return usd >= 1 ? `$${usd.toFixed(2)}` : usd >= 0.01 ? `$${usd.toFixed(3)}` : `$${usd.toFixed(4)}`;
}

function niceTicks(max: number) {
  const raw = max / 4;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let v = 0; v <= max + step * 0.001; v += step) out.push(Math.round(v * 100) / 100);
  if (out.at(-1)! < max) out.push(out.at(-1)! + step);
  return step < 1 ? [0, 1] : out;
}
