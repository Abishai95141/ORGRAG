"use client";

import * as DialogPrimitive from "@radix-ui/react-dialog";
import * as DropdownPrimitive from "@radix-ui/react-dropdown-menu";
import * as SelectPrimitive from "@radix-ui/react-select";
import * as SwitchPrimitive from "@radix-ui/react-switch";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { forwardRef, useId, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type TextareaHTMLAttributes } from "react";
import { IconCheck, IconChevronDown, IconX, Spinner } from "@/components/icons";
import { cn } from "@/lib/utils";

/* ---------------------------------- Button ---------------------------------- */

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger" | "outline";
  size?: "sm" | "md" | "lg" | "icon" | "icon-sm";
  loading?: boolean;
};

const variants = {
  primary: "bg-ink text-bg hover:bg-ink/88 shadow-[inset_0_-1px_0_rgba(0,0,0,.2)]",
  secondary: "bg-surface text-ink border border-line hover:border-line-strong hover:bg-surface-2/60",
  outline: "bg-transparent text-ink border border-line-strong hover:bg-surface-2",
  ghost: "bg-transparent text-ink-2 hover:text-ink hover:bg-surface-2",
  danger: "bg-err text-white hover:bg-err/90",
};

const sizes = {
  sm: "h-8 px-3 text-[13px] gap-1.5",
  md: "h-9 px-3.5 text-sm gap-2",
  lg: "h-11 px-5 text-[15px] gap-2",
  icon: "h-9 w-9 justify-center",
  "icon-sm": "h-7 w-7 justify-center",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", loading, className, children, disabled, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cn(
        "inline-flex shrink-0 items-center rounded-[var(--radius-ctl)] font-medium whitespace-nowrap transition-colors duration-150 select-none",
        "disabled:pointer-events-none disabled:opacity-50",
        variants[variant],
        sizes[size],
        className,
      )}
      {...rest}
    >
      {loading ? <Spinner size={14} /> : null}
      {children}
    </button>
  );
});

/* ---------------------------------- Inputs ---------------------------------- */

const fieldBase =
  "w-full rounded-[var(--radius-ctl)] border border-line bg-surface text-ink placeholder:text-ink-3 transition-[border-color,box-shadow] duration-150 outline-none focus:border-line-strong focus:shadow-[0_0_0_3px_var(--ring)] disabled:opacity-60";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className, ...rest },
  ref,
) {
  return <input ref={ref} className={cn(fieldBase, "h-9 px-3 text-sm", className)} {...rest} />;
});

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea(
  { className, ...rest },
  ref,
) {
  return <textarea ref={ref} className={cn(fieldBase, "min-h-24 px-3 py-2 text-sm leading-relaxed", className)} {...rest} />;
});

export function Field({
  label,
  hint,
  error,
  children,
  htmlFor,
}: {
  label: string;
  hint?: ReactNode;
  error?: string | null;
  children: ReactNode;
  htmlFor?: string;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={htmlFor} className="text-[13px] font-medium text-ink-2">
        {label}
      </label>
      {children}
      {error ? <p className="text-xs text-err">{error}</p> : hint ? <p className="text-xs text-ink-3">{hint}</p> : null}
    </div>
  );
}

/* ---------------------------------- Dialog ---------------------------------- */

export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  width = 480,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  width?: number;
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-[rgb(20_19_16/0.36)] backdrop-blur-[2px] data-[state=open]:animate-fade" />
        <DialogPrimitive.Content
          className="fixed top-[12vh] left-1/2 z-50 flex max-h-[80vh] w-[calc(100vw-32px)] -translate-x-1/2 flex-col rounded-[var(--radius-card)] border border-line bg-surface shadow-pop data-[state=open]:animate-rise"
          style={{ maxWidth: width }}
        >
          <div className="flex items-start justify-between gap-4 px-5 pt-5">
            <div>
              <DialogPrimitive.Title className="text-[15px] font-semibold tracking-tight">{title}</DialogPrimitive.Title>
              {description ? (
                <DialogPrimitive.Description className="mt-1 text-[13px] leading-relaxed text-ink-3">
                  {description}
                </DialogPrimitive.Description>
              ) : (
                <DialogPrimitive.Description className="sr-only">{title}</DialogPrimitive.Description>
              )}
            </div>
            <DialogPrimitive.Close asChild>
              <Button variant="ghost" size="icon-sm" aria-label="Close" className="-mt-1 -mr-1">
                <IconX size={16} />
              </Button>
            </DialogPrimitive.Close>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
          {footer ? <div className="flex justify-end gap-2 border-t border-line px-5 py-3">{footer}</div> : null}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

/** Right-side sheet (document preview, source details). */
export function Sheet({
  open,
  onOpenChange,
  title,
  subtitle,
  children,
  width = 560,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  subtitle?: ReactNode;
  children: ReactNode;
  width?: number;
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-[rgb(20_19_16/0.28)] data-[state=open]:animate-fade" />
        <DialogPrimitive.Content
          className="fixed inset-y-2 right-2 z-50 flex w-[calc(100vw-16px)] flex-col rounded-[var(--radius-card)] border border-line bg-surface shadow-pop data-[state=open]:animate-[rise_220ms_cubic-bezier(.2,.7,.2,1)]"
          style={{ maxWidth: width }}
        >
          <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
            <div className="min-w-0">
              <DialogPrimitive.Title className="truncate text-[15px] font-semibold tracking-tight">{title}</DialogPrimitive.Title>
              <DialogPrimitive.Description asChild>
                <div className="mt-1 text-[12px] text-ink-3">{subtitle}</div>
              </DialogPrimitive.Description>
            </div>
            <DialogPrimitive.Close asChild>
              <Button variant="ghost" size="icon-sm" aria-label="Close">
                <IconX size={16} />
              </Button>
            </DialogPrimitive.Close>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

/* ---------------------------------- Menu ---------------------------------- */

export const Menu = DropdownPrimitive.Root;
export const MenuTrigger = DropdownPrimitive.Trigger;

export function MenuContent({
  children,
  align = "start",
  side = "bottom",
  className,
  sideOffset = 6,
}: {
  children: ReactNode;
  align?: "start" | "center" | "end";
  side?: "top" | "bottom" | "left" | "right";
  className?: string;
  sideOffset?: number;
}) {
  return (
    <DropdownPrimitive.Portal>
      <DropdownPrimitive.Content
        align={align}
        side={side}
        sideOffset={sideOffset}
        className={cn(
          "z-50 min-w-48 rounded-[11px] border border-line bg-surface p-1 shadow-pop data-[state=open]:animate-fade",
          className,
        )}
      >
        {children}
      </DropdownPrimitive.Content>
    </DropdownPrimitive.Portal>
  );
}

export function MenuItem({
  children,
  onSelect,
  danger,
  icon,
  disabled,
  trailing,
}: {
  children: ReactNode;
  onSelect?: (e: Event) => void;
  danger?: boolean;
  icon?: ReactNode;
  disabled?: boolean;
  trailing?: ReactNode;
}) {
  return (
    <DropdownPrimitive.Item
      onSelect={onSelect}
      disabled={disabled}
      className={cn(
        "flex h-8 cursor-default items-center gap-2.5 rounded-[7px] px-2 text-[13px] outline-none select-none",
        "data-[disabled]:opacity-50 data-[highlighted]:bg-surface-2",
        danger ? "text-err" : "text-ink",
      )}
    >
      {icon ? <span className={cn("grid w-4 place-items-center", danger ? "text-err" : "text-ink-3")}>{icon}</span> : null}
      <span className="min-w-0 flex-1 truncate">{children}</span>
      {trailing}
    </DropdownPrimitive.Item>
  );
}

export function MenuCheckItem({
  children,
  checked,
  onCheckedChange,
  icon,
}: {
  children: ReactNode;
  checked: boolean;
  onCheckedChange: (v: boolean) => void;
  icon?: ReactNode;
}) {
  return (
    <DropdownPrimitive.CheckboxItem
      checked={checked}
      onCheckedChange={onCheckedChange}
      onSelect={(e) => e.preventDefault()}
      className="flex h-8 cursor-default items-center gap-2.5 rounded-[7px] px-2 text-[13px] outline-none select-none data-[highlighted]:bg-surface-2"
    >
      {icon}
      <span className="min-w-0 flex-1 truncate">{children}</span>
      <span
        className={cn(
          "grid h-4 w-4 place-items-center rounded-[5px] border transition-colors",
          checked ? "border-ink bg-ink text-bg" : "border-line-strong",
        )}
      >
        {checked ? <IconCheck size={12} strokeWidth={2} /> : null}
      </span>
    </DropdownPrimitive.CheckboxItem>
  );
}

export const MenuSeparator = () => <DropdownPrimitive.Separator className="my-1 h-px bg-line" />;
export const MenuLabel = ({ children }: { children: ReactNode }) => (
  <DropdownPrimitive.Label className="px-2 pt-1.5 pb-1 font-mono text-[10.5px] tracking-wider text-ink-3 uppercase">
    {children}
  </DropdownPrimitive.Label>
);

/* ---------------------------------- Tooltip ---------------------------------- */

export function Tooltip({ label, children, side = "top" }: { label: ReactNode; children: ReactNode; side?: "top" | "bottom" | "left" | "right" }) {
  return (
    <TooltipPrimitive.Root delayDuration={350}>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          side={side}
          sideOffset={6}
          className="z-[60] rounded-md bg-ink px-2 py-1 text-[12px] text-bg shadow-pop data-[state=delayed-open]:animate-fade"
        >
          {label}
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  );
}

/* ---------------------------------- Select ---------------------------------- */

export function Select<T extends string>({
  value,
  onValueChange,
  options,
  placeholder,
  className,
  id,
}: {
  value: T | undefined;
  onValueChange: (v: T) => void;
  options: { value: T; label: ReactNode; hint?: string }[];
  placeholder?: string;
  className?: string;
  id?: string;
}) {
  return (
    <SelectPrimitive.Root value={value} onValueChange={(v) => onValueChange(v as T)}>
      <SelectPrimitive.Trigger
        id={id}
        className={cn(fieldBase, "flex h-9 items-center justify-between gap-2 px-3 text-left text-sm", className)}
      >
        <SelectPrimitive.Value placeholder={placeholder} />
        <SelectPrimitive.Icon className="text-ink-3">
          <IconChevronDown size={16} />
        </SelectPrimitive.Icon>
      </SelectPrimitive.Trigger>
      <SelectPrimitive.Portal>
        <SelectPrimitive.Content
          position="popper"
          sideOffset={6}
          className="z-[60] max-h-72 min-w-[var(--radix-select-trigger-width)] overflow-hidden rounded-[11px] border border-line bg-surface p-1 shadow-pop"
        >
          <SelectPrimitive.Viewport>
            {options.map((o) => (
              <SelectPrimitive.Item
                key={o.value}
                value={o.value}
                className="flex cursor-default items-center justify-between gap-3 rounded-[7px] px-2 py-1.5 text-[13px] outline-none select-none data-[highlighted]:bg-surface-2"
              >
                <div className="min-w-0">
                  <SelectPrimitive.ItemText>{o.label}</SelectPrimitive.ItemText>
                  {o.hint ? <div className="text-[11.5px] text-ink-3">{o.hint}</div> : null}
                </div>
                <SelectPrimitive.ItemIndicator>
                  <IconCheck size={14} />
                </SelectPrimitive.ItemIndicator>
              </SelectPrimitive.Item>
            ))}
          </SelectPrimitive.Viewport>
        </SelectPrimitive.Content>
      </SelectPrimitive.Portal>
    </SelectPrimitive.Root>
  );
}

/* ---------------------------------- Switch ---------------------------------- */

export function Switch({
  checked,
  onCheckedChange,
  label,
  description,
}: {
  checked: boolean;
  onCheckedChange: (v: boolean) => void;
  label: string;
  description?: ReactNode;
}) {
  const id = useId();
  return (
    <div className="flex items-start justify-between gap-6">
      <label htmlFor={id} className="min-w-0">
        <div className="text-sm font-medium">{label}</div>
        {description ? <div className="mt-0.5 text-[13px] text-ink-3">{description}</div> : null}
      </label>
      <SwitchPrimitive.Root
        id={id}
        checked={checked}
        onCheckedChange={onCheckedChange}
        className="relative mt-0.5 h-5 w-9 shrink-0 rounded-full bg-surface-3 transition-colors data-[state=checked]:bg-ink"
      >
        <SwitchPrimitive.Thumb className="block h-4 w-4 translate-x-0.5 rounded-full bg-surface shadow transition-transform data-[state=checked]:translate-x-[18px]" />
      </SwitchPrimitive.Root>
    </div>
  );
}

/* ---------------------------------- Segmented ---------------------------------- */

export function Segmented<T extends string>({
  value,
  onChange,
  options,
  size = "md",
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: ReactNode; icon?: ReactNode }[];
  size?: "sm" | "md";
}) {
  return (
    <div role="radiogroup" className="inline-flex rounded-[10px] border border-line bg-surface-2 p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-[8px] font-medium transition-all duration-150",
            size === "sm" ? "h-7 px-2.5 text-[12.5px]" : "h-8 px-3 text-[13px]",
            value === o.value ? "bg-surface text-ink shadow-[0_1px_2px_rgba(0,0,0,.08),0_0_0_1px_var(--line)]" : "text-ink-3 hover:text-ink-2",
          )}
        >
          {o.icon}
          {o.label}
        </button>
      ))}
    </div>
  );
}

/* ---------------------------------- Display ---------------------------------- */

export function Badge({
  children,
  tone = "neutral",
  className,
}: {
  children: ReactNode;
  tone?: "neutral" | "accent" | "ok" | "warn" | "err" | "outline";
  className?: string;
}) {
  const tones = {
    neutral: "bg-surface-2 text-ink-2",
    accent: "bg-accent-soft text-accent",
    ok: "bg-ok-soft text-ok",
    warn: "bg-warn-soft text-warn",
    err: "bg-err-soft text-err",
    outline: "border border-line text-ink-2",
  };
  return (
    <span className={cn("inline-flex h-5 items-center gap-1 rounded-[6px] px-1.5 text-[11.5px] font-medium", tones[tone], className)}>
      {children}
    </span>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="inline-flex h-5 min-w-5 items-center justify-center rounded-[5px] border border-line bg-surface px-1 font-mono text-[10.5px] text-ink-3">
      {children}
    </kbd>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("strata-pulse rounded-md bg-surface-2", className)} />;
}

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("rounded-[var(--radius-card)] border border-line bg-surface", className)}>{children}</div>;
}

export function PageHeader({
  title,
  description,
  actions,
  eyebrow,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  eyebrow?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4 pb-6">
      <div className="min-w-0">
        {eyebrow ? <div className="mb-2 font-mono text-[11px] tracking-wider text-ink-3 uppercase">{eyebrow}</div> : null}
        <h1 className="font-display text-[34px] leading-[1.05] tracking-tight">{title}</h1>
        {description ? <p className="mt-2 max-w-xl text-[14px] leading-relaxed text-ink-3">{description}</p> : null}
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function EmptyState({
  art,
  title,
  description,
  action,
}: {
  art: ReactNode;
  title: string;
  description?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center px-6 py-14 text-center">
      <div className="mb-6 w-full max-w-[220px]">{art}</div>
      <h2 className="font-display text-[26px] leading-tight tracking-tight">{title}</h2>
      {description ? <p className="mt-2 max-w-sm text-[14px] leading-relaxed text-ink-3">{description}</p> : null}
      {action ? <div className="mt-6">{action}</div> : null}
    </div>
  );
}

export function Stat({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <div className="font-mono text-[10.5px] tracking-wider text-ink-3 uppercase">{label}</div>
      <div className="text-[28px] leading-none font-semibold tracking-tight tabular-nums">{value}</div>
      {sub ? <div className="text-[12px] text-ink-3">{sub}</div> : null}
    </div>
  );
}

/** Thin progress rule with a moving highlight for indeterminate states. */
export function Progress({ value, className }: { value: number | null; className?: string }) {
  return (
    <div className={cn("relative h-1 overflow-hidden rounded-full bg-surface-3", className)}>
      {value === null ? (
        <div className="absolute inset-y-0 w-1/3 animate-[indeterminate_1.2s_ease-in-out_infinite] rounded-full bg-accent" />
      ) : (
        <div className="h-full rounded-full bg-accent transition-[width] duration-300" style={{ width: `${Math.max(2, Math.min(100, value))}%` }} />
      )}
      <style>{`@keyframes indeterminate{0%{left:-35%}100%{left:100%}}`}</style>
    </div>
  );
}
