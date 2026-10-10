"use client";

import { useEffect, useRef, useState } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { useImageTrace } from "@/hooks/use-image-trace";
import { IMAGE_TYPES, imageFileProblem } from "@/lib/image/file";
import { defaultSelection, imageShape, noRingsProblem, type Ring } from "@/lib/image/rings";
import type { Stroke } from "@/lib/shape/types";

type Props = {
  /** Studio 目前是否已有圖片圖形（例如從 localStorage 還原）。 */
  hasShape: boolean;
  /** 選取的輪廓改變時，回傳正規化後的封閉筆畫。 */
  onStrokes: (strokes: Stroke[]) => void;
};

/** 輪廓圖底下的原圖預覽。object URL 在 effect 裡建立、在 cleanup 釋放（換檔案或元件離開時），不經過 state。 */
function PreviewImage({ file, width, height }: { file: File; width: number; height: number }) {
  const ref = useRef<SVGImageElement>(null);
  useEffect(() => {
    const url = URL.createObjectURL(file);
    ref.current?.setAttribute("href", url);
    return () => URL.revokeObjectURL(url);
  }, [file]);
  return <image ref={ref} width={width} height={height} opacity={0.35} />;
}

export function ImageSource({ hasShape, onStrokes }: Props) {
  const [rings, setRings] = useState<Ring[]>([]);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [selected, setSelected] = useState<number[]>([]);
  const [threshold, setThreshold] = useState(128);
  const [invert, setInvert] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const loaded = size.width > 0;
  const noRings = noRingsProblem(loaded, rings);

  const { trace, working, error } = useImageTrace((result) => {
    const initial = defaultSelection(result.rings);
    setRings(result.rings);
    setSize({ width: result.width, height: result.height });
    setThreshold(result.threshold);
    setSelected(initial);
    onStrokes(imageShape(result.rings, initial).strokes);
  });

  const onFile = (picked: File | undefined) => {
    if (!picked) return;
    const problem = imageFileProblem(picked);
    setFileError(problem);
    if (problem) return;
    // 先清掉舊圖：新圖解碼失敗時，畫面不會留著舊輪廓卻配上壞掉的預覽
    setRings([]);
    setSelected([]);
    setSize({ width: 0, height: 0 });
    setFile(picked);
    trace({ file: picked, threshold: null, invert });
  };

  const toggle = (index: number) => {
    const next = selected.includes(index) ? selected.filter((i) => i !== index) : [...selected, index];
    setSelected(next);
    onStrokes(imageShape(rings, next).strokes);
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="source-image">圖片（PNG、JPG、WebP，最大 5 MB）</Label>
        <Input
          id="source-image"
          type="file"
          accept={IMAGE_TYPES.join(",")}
          onChange={(e) => onFile(e.target.files?.[0])}
        />
        {fileError && (
          <p role="alert" className="text-sm text-destructive">
            {fileError}
          </p>
        )}
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        {!loaded && hasShape && (
          <p className="text-xs text-muted-foreground">使用上次儲存的圖形；重新上傳圖片可以調整輪廓。</p>
        )}
      </div>

      {/* Radix 的滑桿把手是 span[role=slider]，<label> 命名不到它，shadcn 的 Slider 也不會把 aria-label 轉給把手，所以用群組命名 */}
      <div role="group" aria-labelledby="source-threshold-label" className="flex flex-col gap-2">
        <Label id="source-threshold-label">黑白門檻：{threshold}</Label>
        <Slider
          min={0}
          max={255}
          step={1}
          value={[threshold]}
          // 不能在處理中 disabled：Radix 停用時會拿掉把手的 tabIndex，鍵盤每按一次方向鍵就掉焦點。
          // 連續請求由 hook（只取最新一次）與 worker（依序處理）保證結果一致。
          disabled={!loaded}
          onValueChange={([value]) => setThreshold(value)}
          onValueCommit={([value]) => trace({ threshold: value, invert })}
        />
      </div>

      <div className="flex items-center gap-2">
        <Switch
          id="source-invert"
          checked={invert}
          disabled={!loaded}
          onCheckedChange={(checked) => {
            setInvert(checked);
            trace({ threshold, invert: checked });
          }}
        />
        <Label htmlFor="source-invert">反轉（深色背景、淺色圖案）</Label>
      </div>

      {working && (
        <p role="status" className="text-sm text-muted-foreground">
          處理中…
        </p>
      )}

      {loaded && (
        <div className="flex flex-col gap-1.5">
          {noRings ? (
            <p role="alert" className="text-sm text-destructive">
              {noRings}
            </p>
          ) : (
            <p className="text-xs text-muted-foreground">
              點輪廓加入或移除（藍色＝已選，{selected.length} 個）。預設選最大的外框。
            </p>
          )}
          <svg
            viewBox={`0 0 ${size.width} ${size.height}`}
            className="w-full rounded-md border bg-white"
            role="group"
            aria-label="輪廓選取"
          >
            {file && <PreviewImage file={file} width={size.width} height={size.height} />}
            {rings.map((ring, i) => (
              <polygon
                key={i}
                points={ring.points.map((p) => p.join(",")).join(" ")}
                fill={selected.includes(i) ? "#2563eb" : "transparent"}
                fillOpacity={selected.includes(i) ? 0.25 : 1}
                stroke={selected.includes(i) ? "#2563eb" : "#64748b"}
                strokeWidth={Math.max(1, size.width / 200)}
                className="cursor-pointer"
                onClick={() => toggle(i)}
              />
            ))}
          </svg>
        </div>
      )}
    </div>
  );
}
