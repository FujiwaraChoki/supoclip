"use client"

import { useCallback, useEffect, useRef, useState } from "react"

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"

interface TranscriptPreviewProps {
  text: string
  clipTitle?: string | null
}

export function TranscriptPreview({ text, clipTitle }: TranscriptPreviewProps) {
  const previewRef = useRef<HTMLParagraphElement>(null)
  const [isTruncated, setIsTruncated] = useState(false)

  const measureTruncation = useCallback(() => {
    const preview = previewRef.current
    if (!preview) return

    setIsTruncated(preview.scrollHeight > preview.clientHeight + 1)
  }, [])

  useEffect(() => {
    measureTruncation()

    const preview = previewRef.current
    if (!preview || typeof ResizeObserver === "undefined") return

    const observer = new ResizeObserver(measureTruncation)
    observer.observe(preview)

    return () => observer.disconnect()
  }, [measureTruncation, text])

  return (
    <div className="mb-4">
      <h4 className="mb-2 font-medium text-black">Transcript</h4>
      <div className="rounded-lg bg-gray-50 p-3">
        <p ref={previewRef} className="line-clamp-4 text-sm leading-6 text-gray-700">
          {text}
        </p>

        {isTruncated && (
          <Dialog>
            <DialogTrigger asChild>
              <button
                type="button"
                className="mt-2 rounded-sm text-sm font-medium text-gray-900 underline decoration-gray-300 underline-offset-4 transition-colors hover:decoration-gray-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gray-400 focus-visible:ring-offset-2"
              >
                See more
              </button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Full transcript</DialogTitle>
                <DialogDescription>
                  {clipTitle ? `Transcript for ${clipTitle}` : "Complete transcript for this clip"}
                </DialogDescription>
              </DialogHeader>
              <div className="max-h-[60vh] overflow-y-auto rounded-lg bg-gray-50 p-4 text-sm leading-6 text-gray-700 whitespace-pre-wrap">
                {text}
              </div>
            </DialogContent>
          </Dialog>
        )}
      </div>
    </div>
  )
}
