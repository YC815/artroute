"use client";

import { Trash2 } from "lucide-react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { DraftsView } from "@/hooks/use-drafts";
import { formatModified, isOutdatedRoute } from "@/lib/drafts/draft-info";
import { thumbnailPath } from "@/lib/drafts/thumbnail";
import { draftNoticeMessage } from "@/lib/edit/edit-hint";
import { formatDataVersion, formatDistance } from "@/lib/format";
import type { Draft } from "@/lib/storage/drafts";
import { gpxName } from "@/lib/studio/state";

type Props = {
  view: DraftsView;
  /** 目前打開（微調中）的草稿 id。 */
  openId: string | null;
  /** 引擎目前的路網版本；null（連不上）時不標舊路網。 */
  engineVersion: string | null;
  onOpen: (draft: Draft) => void;
  onRemove: (draft: Draft) => void;
};

const THUMB_W = 72;
const THUMB_H = 54;

function DraftItem({ draft, open, engineVersion, onOpen, onRemove }: {
  draft: Draft;
  open: boolean;
  engineVersion: string | null;
  onOpen: () => void;
  onRemove: () => void;
}) {
  const name = gpxName(draft.source);
  const outdated = isOutdatedRoute(draft.route.dataVersion, engineVersion);
  return (
    <li className="flex items-center gap-3 rounded-lg border p-2">
      <svg
        viewBox={`0 0 ${THUMB_W} ${THUMB_H}`}
        width={THUMB_W}
        height={THUMB_H}
        className="shrink-0 rounded bg-muted text-blue-600"
        role="img"
        aria-label={`${name} 的路線縮圖`}
      >
        <path
          d={thumbnailPath(draft.route.segments, THUMB_W, THUMB_H)}
          fill="none"
          stroke="currentColor"
          strokeWidth={1.5}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="flex items-center gap-1.5">
          <span className="truncate text-sm font-medium">{name}</span>
          {open && <Badge variant="secondary">編輯中</Badge>}
        </div>
        <span className="text-xs text-muted-foreground tabular-nums">
          {formatDistance(draft.route.lengthM)} · {formatModified(draft.updatedAt)}
        </span>
        <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
          路網 {formatDataVersion(draft.route.dataVersion)}
          {outdated && <Badge variant="outline">舊路網</Badge>}
        </span>
      </div>
      <div className="flex shrink-0 flex-col gap-1">
        <Button size="xs" variant="outline" disabled={open} onClick={onOpen}>
          打開
        </Button>
        <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button size="xs" variant="ghost" aria-label={`刪除 ${name}`}>
              <Trash2 />
              刪除
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>刪除這份草稿？</AlertDialogTitle>
              <AlertDialogDescription>
                「{name}」（{formatDistance(draft.route.lengthM)}，{formatModified(draft.updatedAt)} 修改）刪除後無法復原。
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>取消</AlertDialogCancel>
              <AlertDialogAction variant="destructive" onClick={onRemove}>
                刪除
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>
    </li>
  );
}

/** 「我的路線」：微調過的路線自動存成的草稿。只能一份一份刪（要確認），沒有「全部刪除」。 */
export function DraftsPanel({ view, openId, engineVersion, onOpen, onRemove }: Props) {
  return (
    <section className="flex flex-col gap-2" aria-label="我的路線">
      <h2 className="text-sm font-semibold">我的路線</h2>
      {view.unavailable && <p className="text-xs text-muted-foreground">這個瀏覽器不能儲存草稿，微調過的路線不會保留。</p>}
      {view.actionError && (
        <p role="alert" className="text-xs text-destructive">
          {view.actionError}
        </p>
      )}
      {view.overLimit && (
        <p role="status" className="text-xs text-destructive">
          {draftNoticeMessage("over_limit")}
        </p>
      )}
      {view.loaded && !view.unavailable && view.drafts.length === 0 && (
        <p className="text-xs text-muted-foreground">還沒有草稿。微調過的路線會自動存在這裡（沒打開過的 7 天後自動刪除）。</p>
      )}
      <ul className="flex flex-col gap-2">
        {view.drafts.map((draft) => (
          <DraftItem
            key={draft.id}
            draft={draft}
            open={draft.id === openId}
            engineVersion={engineVersion}
            onOpen={() => onOpen(draft)}
            onRemove={() => onRemove(draft)}
          />
        ))}
      </ul>
    </section>
  );
}
