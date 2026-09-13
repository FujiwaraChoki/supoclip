import type { LucideIcon } from "lucide-react";
import Link from "next/link";
import { Button } from "@/components/ui/button";

interface EmptyStateAction {
  label: string;
  onClick?: () => void;
  href?: string;
}

interface EmptyStateProps {
  icon: LucideIcon;
  title: string;
  description?: string;
  action?: EmptyStateAction;
  className?: string;
}

export function EmptyState({ icon: Icon, title, description, action, className }: EmptyStateProps) {
  return (
    <div
      className={`text-center py-16 rounded-xl border border-dashed border-stone-300 dark:border-stone-700 ${className ?? ""}`}
    >
      <Icon className="w-10 h-10 text-stone-300 dark:text-stone-600 mx-auto mb-3" />
      <p className="font-medium text-stone-700 dark:text-stone-300 mb-1">{title}</p>
      {description && <p className="text-sm text-stone-500 mb-4">{description}</p>}
      {action &&
        (action.href ? (
          <Link href={action.href}>
            <Button>{action.label}</Button>
          </Link>
        ) : (
          <Button onClick={action.onClick}>{action.label}</Button>
        ))}
    </div>
  );
}
