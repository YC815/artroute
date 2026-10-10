"use client";

import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { FONT_IDS, FONT_LABELS, type HersheyFontId } from "@/lib/text/hershey";
import { MAX_LINE_CHARS, MAX_LINES, validateText } from "@/lib/text/layout";

// Hershey 字型授權要求隨字型資料附上的兩句英文致謝，原文照錄（與 lib/text/hershey.ts 標頭、
// hersheytext 套件 svg_fonts 的 metadata 一致），不翻譯、不刪節。
const HERSHEY_ATTRIBUTION = [
  "The Hershey Fonts were originally created by Dr. A. V. Hershey while working at the U. S. National Bureau of Standards.",
  "The format of the Font data in this distribution was originally created by James Hurt, Cognition, Inc., 900 Technology Park Drive, Billerica, MA 01821 (mit-eddie!ci-dandelion!hurt)",
];

type Props = {
  text: string;
  font: HersheyFontId;
  onChange: (text: string, font: HersheyFontId) => void;
};

export function TextSource({ text, font, onChange }: Props) {
  const problem = validateText(text);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="source-text">文字</Label>
        <Textarea
          id="source-text"
          rows={2}
          value={text}
          aria-invalid={problem !== null}
          onChange={(e) => onChange(e.target.value, font)}
        />
        <p className="text-xs text-muted-foreground">
          英文字母、數字與 ♥ ! ? &amp; . -，最多 {MAX_LINES} 行、每行 {MAX_LINE_CHARS} 字（空白也算）。
        </p>
        {problem && <p className="text-sm text-destructive">{problem}</p>}
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="source-font">字型</Label>
        <Select value={font} onValueChange={(value) => onChange(text, value as HersheyFontId)}>
          <SelectTrigger id="source-font" className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {FONT_IDS.map((id) => (
              <SelectItem key={id} value={id}>
                {FONT_LABELS[id]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="flex flex-col gap-1 text-[11px] leading-snug text-muted-foreground">
        <p>字型資料取自 hersheytext 套件，授權要求附上以下致謝：</p>
        <div lang="en" className="flex flex-col gap-1">
          {HERSHEY_ATTRIBUTION.map((sentence) => (
            <p key={sentence}>{sentence}</p>
          ))}
        </div>
      </div>
    </div>
  );
}
