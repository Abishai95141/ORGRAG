"use client";

import { Field, Input, Switch, Textarea } from "@/components/ui";

/** The subset of JSON Schema connector config forms use. */
export type PropSpec = {
  type?: "string" | "integer" | "number" | "boolean" | "array";
  title?: string;
  format?: string;
  secret?: boolean;
  default?: unknown;
  minimum?: number;
  maximum?: number;
};
export type ConfigSchema = { properties?: Record<string, PropSpec>; required?: string[] };

export type FormValues = Record<string, string | boolean>;

/** Starting form values: schema defaults, else empty. */
export function initialValues(schema: ConfigSchema): FormValues {
  const out: FormValues = {};
  for (const [name, spec] of Object.entries(schema.properties ?? {})) {
    out[name] = spec.type === "boolean" ? spec.default !== false : spec.default !== undefined ? String(spec.default) : "";
  }
  return out;
}

/** Form values → connector config. Arrays are comma or newline separated; empty fields are left out. */
export function toConfig(schema: ConfigSchema, values: FormValues): Record<string, unknown> {
  const config: Record<string, unknown> = {};
  for (const [name, spec] of Object.entries(schema.properties ?? {})) {
    const v = values[name];
    if (spec.type === "boolean") config[name] = !!v;
    else if (typeof v !== "string" || !v.trim()) continue;
    else if (spec.type === "array") config[name] = v.split(/[\n,]/).map((x) => x.trim()).filter(Boolean);
    else if (spec.type === "integer" || spec.type === "number") config[name] = Number(v);
    else config[name] = v.trim();
  }
  return config;
}

/** Secrets the user must provide when creating a source (connectors don't mark them required,
 * so editing a source doesn't demand them again). */
export function missingSecrets(schema: ConfigSchema, values: FormValues) {
  return Object.entries(schema.properties ?? {})
    .filter(([name, spec]) => spec.secret && !String(values[name] ?? "").trim())
    .map(([name]) => name);
}

/** Splits a title like "Channels (names or IDs; …)" into a label and a hint. A one-word
 * parenthetical such as "(xoxb-…)" is a format example, so it becomes the placeholder. */
function labelParts(title: string) {
  const m = title.match(/^(.*?)\s*\((.*)\)$/);
  if (!m) return { label: title, hint: undefined, example: undefined };
  if (!/\s/.test(m[2])) return { label: m[1], hint: undefined, example: m[2] };
  return { label: m[1], hint: m[2].charAt(0).toUpperCase() + m[2].slice(1), example: undefined };
}

export function SchemaForm({
  schema,
  values,
  onChange,
  idPrefix,
}: {
  schema: ConfigSchema;
  values: FormValues;
  onChange: (v: FormValues) => void;
  idPrefix: string;
}) {
  const props = Object.entries(schema.properties ?? {});
  // Secrets first: they're what the user must fetch from the other service.
  props.sort(([, a], [, b]) => Number(!!b.secret) - Number(!!a.secret));
  return (
    <>
      {props.map(([name, spec], i) => {
        const id = `${idPrefix}-${name}`;
        const { label, hint, example } = labelParts(spec.title ?? name);
        const set = (v: string | boolean) => onChange({ ...values, [name]: v });
        if (spec.type === "boolean") {
          return <Switch key={name} checked={!!values[name]} onCheckedChange={set} label={label} description={hint} />;
        }
        const fieldHint = spec.secret ? `${hint ? `${hint}. ` : ""}Stored server-side and never shown again.` : hint;
        return (
          <Field key={name} label={label} htmlFor={id} hint={fieldHint}>
            {spec.format === "textarea" || spec.type === "array" ? (
              <Textarea
                id={id}
                value={String(values[name] ?? "")}
                onChange={(e) => set(e.target.value)}
                autoFocus={i === 0}
                spellCheck={false}
                className={spec.format === "textarea" ? "min-h-28 font-mono text-[12px]" : "min-h-16"}
                placeholder={spec.type === "array" ? "One per line, or comma separated" : spec.secret ? '{ "type": "service_account", … }' : undefined}
              />
            ) : (
              <Input
                id={id}
                type={spec.secret ? "password" : spec.type === "integer" || spec.type === "number" ? "number" : spec.format === "email" ? "email" : "text"}
                autoComplete="off"
                min={spec.minimum}
                max={spec.maximum}
                value={String(values[name] ?? "")}
                onChange={(e) => set(e.target.value)}
                autoFocus={i === 0}
                placeholder={example}
              />
            )}
          </Field>
        );
      })}
    </>
  );
}
