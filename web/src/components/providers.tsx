"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { Toaster } from "sonner";
import { useState } from "react";
import { ApiError } from "@/lib/api";
import { ConfirmHost } from "@/components/ui/confirm";

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 15_000,
            refetchOnWindowFocus: false,
            retry: (count, err) => !(err instanceof ApiError && err.status < 500) && count < 2,
          },
        },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      <TooltipPrimitive.Provider>
        {children}
        <ConfirmHost />
        <Toaster
          position="bottom-right"
          toastOptions={{
            className: "!rounded-[12px] !border !border-line !bg-surface !text-ink !shadow-pop !text-[13px]",
          }}
        />
      </TooltipPrimitive.Provider>
    </QueryClientProvider>
  );
}
