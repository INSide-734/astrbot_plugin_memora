import type { PropsWithChildren, ReactNode } from "react";

import { Badge } from "@/components/ui/Badge";

export function DetailSection({
  title,
  children,
}: PropsWithChildren<{ title?: ReactNode }>) {
  return (
    <section className="space-y-3">
      {title ? <h3 className="text-sm font-medium text-foreground">{title}</h3> : null}
      {children}
    </section>
  );
}

export function DetailGrid({ children }: PropsWithChildren) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2">
      {children}
    </dl>
  );
}

export function DetailField({
  label,
  children,
}: {
  label: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="min-w-0">
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className="mt-1 break-words text-sm text-foreground">{children ?? "--"}</dd>
    </div>
  );
}

export function DetailText({ children }: PropsWithChildren) {
  return <div className="whitespace-pre-wrap break-words text-sm leading-6">{children}</div>;
}

export function DetailTags({
  tags,
  emptyLabel = "--",
}: {
  tags: readonly string[];
  emptyLabel?: string;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {tags.length ? (
        tags.map((tag) => <Badge key={tag} variant="secondary">{tag}</Badge>)
      ) : (
        <span className="text-sm text-muted-foreground">{emptyLabel}</span>
      )}
    </div>
  );
}
