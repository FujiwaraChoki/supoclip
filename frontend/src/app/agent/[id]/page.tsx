"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { ArrowLeft, MoreHorizontal, Pencil, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { AgentChat } from "@/components/agent/agent-chat";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { agentKeys, deleteConversation, fetchConversation, renameConversation } from "@/lib/agent";

export default function AgentConversationPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const t = useTranslations("agent");
  const router = useRouter();
  const queryClient = useQueryClient();
  // A chat just started on the home screen replays its stream from the start. Kept for this
  // visit only: the address loses "?new=1" so a reload doesn't replay it again.
  const startedHere = useSearchParams().get("new") === "1";
  const [freshlyStarted] = useState(startedHere);
  useEffect(() => {
    if (startedHere) router.replace(`/agent/${id}`, { scroll: false });
  }, [startedHere, router, id]);
  const { data } = useQuery({ queryKey: agentKeys.conversation(id), queryFn: () => fetchConversation(id), staleTime: Infinity, refetchOnWindowFocus: false });
  const [renaming, setRenaming] = useState(false);
  const title = data?.conversation.title || t("home.untitled");

  const rename = async (value: string) => {
    setRenaming(false);
    const next = value.trim();
    if (!next || next === data?.conversation.title) return;
    try {
      await renameConversation(id, next);
      await queryClient.invalidateQueries({ queryKey: agentKeys.conversation(id) });
      void queryClient.invalidateQueries({ queryKey: agentKeys.conversations });
    } catch {
      toast.error(t("errors.rename"));
    }
  };

  const remove = async () => {
    try {
      await deleteConversation(id);
      void queryClient.invalidateQueries({ queryKey: agentKeys.conversations });
      router.push("/");
    } catch {
      toast.error(t("errors.delete"));
    }
  };

  return (
    <div className="relative">
      <header className="sticky top-14 z-30 flex h-12 items-center gap-2 border-b bg-canvas/85 px-3 backdrop-blur md:top-0 sm:px-4">
        <Button asChild variant="ghost" size="icon" className="size-8 rounded-full" aria-label={t("chat.back")}>
          <Link href="/"><ArrowLeft className="size-4" /></Link>
        </Button>
        {renaming ? (
          <input
            autoFocus
            defaultValue={data?.conversation.title ?? ""}
            onBlur={(event) => void rename(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void rename(event.currentTarget.value);
              if (event.key === "Escape") setRenaming(false);
            }}
            className="h-8 min-w-0 flex-1 rounded-md border bg-background px-2 text-sm outline-none"
            aria-label={t("chat.rename")}
          />
        ) : (
          <h1 className="min-w-0 flex-1 truncate text-sm font-medium">{title}</h1>
        )}
        <Button asChild variant="ghost" size="sm" className="h-8 rounded-full text-muted-foreground">
          <Link href="/"><Plus className="size-4" /><span className="hidden sm:inline">{t("chat.newChat")}</span></Link>
        </Button>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" className="size-8 rounded-full" aria-label={t("chat.more")}><MoreHorizontal className="size-4" /></Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onSelect={() => setRenaming(true)}><Pencil className="size-4" />{t("chat.rename")}</DropdownMenuItem>
            <DropdownMenuItem onSelect={() => void remove()} className="text-red-600 focus:text-red-600"><Trash2 className="size-4" />{t("chat.delete")}</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </header>
      <AgentChat conversationId={id} freshlyStarted={freshlyStarted} />
    </div>
  );
}
