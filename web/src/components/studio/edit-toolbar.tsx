"use client";

import { Check, LoaderCircle, Move, Redo2, Route, Scissors, Undo2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { draftNoticeMessage, editHint, type DraftNotice } from "@/lib/edit/edit-hint";
import type { EditState, EditTool, RerouteMode } from "@/lib/edit/edit-state";

type Props = {
  state: EditState;
  canUndo: boolean;
  canRedo: boolean;
  draftNotice: DraftNotice | null;
  onTool: (tool: EditTool | null) => void;
  onMode: (mode: RerouteMode) => void;
  onUndo: () => void;
  onRedo: () => void;
  onFinish: () => void;
};

const TOOLS = [
  { value: "cut", label: "剪掉", icon: Scissors },
  { value: "reroute", label: "重畫", icon: Route },
  { value: "drag", label: "拖曳", icon: Move },
] as const;

const isTool = (value: string): value is EditTool => TOOLS.some((t) => t.value === value);

/**
 * 微調工具列：操作（剪掉／重畫／拖曳）、復原、重做、完成；下面是下一步的說明、引擎的回應與草稿的提示。
 * 等待引擎時只剩 Esc（取消）可用，其他按鈕停用。
 */
export function EditToolbar({ state, canUndo, canRedo, draftNotice, onTool, onMode, onUndo, onRedo, onFinish }: Props) {
  const { pending } = state;
  const choosingMode = state.tool === "reroute" && state.picks.length === 2 && state.rerouteMode === null && !pending;
  return (
    <div data-testid="edit-toolbar" className="pointer-events-auto flex max-w-full flex-col gap-2 rounded-lg bg-background/95 p-2 shadow-md ring-1 ring-foreground/10">
      <div className="flex flex-wrap items-center gap-2">
        <ToggleGroup
          type="single"
          variant="outline"
          size="sm"
          aria-label="微調操作"
          value={state.tool ?? ""}
          disabled={pending}
          onValueChange={(value) => onTool(isTool(value) ? value : null)}
        >
          {TOOLS.map(({ value, label, icon: Icon }) => (
            <ToggleGroupItem key={value} value={value} aria-label={label}>
              <Icon />
              {label}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        <div className="flex items-center gap-1">
          <Button size="icon-sm" variant="outline" aria-label="復原" title="復原（⌘/Ctrl+Z）" aria-keyshortcuts="Control+Z Meta+Z" disabled={!canUndo} onClick={onUndo}>
            <Undo2 />
          </Button>
          <Button size="icon-sm" variant="outline" aria-label="重做" title="重做（Shift+⌘/Ctrl+Z）" aria-keyshortcuts="Control+Shift+Z Meta+Shift+Z" disabled={!canRedo} onClick={onRedo}>
            <Redo2 />
          </Button>
        </div>
        <Button size="sm" disabled={pending} onClick={onFinish}>
          <Check />
          完成
        </Button>
      </div>
      {choosingMode && (
        <div role="group" aria-label="重畫的方式" className="flex items-center gap-2">
          <Button size="sm" variant="secondary" onClick={() => onMode("shortest")}>
            最短
          </Button>
          <Button size="sm" variant="secondary" onClick={() => onMode("shape")}>
            貼圖形
          </Button>
        </div>
      )}
      <p role="status" className="flex items-center gap-1.5 text-xs text-muted-foreground">
        {pending && <LoaderCircle className="size-3.5 animate-spin" aria-hidden="true" />}
        {editHint(state)}
        {state.tool !== null && !pending && <span aria-hidden="true"> · Esc 取消</span>}
      </p>
      <div role="alert" className="flex flex-col gap-1 text-xs text-destructive empty:hidden">
        {state.message && <p>{state.message}</p>}
        {draftNotice && <p>{draftNoticeMessage(draftNotice)}</p>}
      </div>
    </div>
  );
}
