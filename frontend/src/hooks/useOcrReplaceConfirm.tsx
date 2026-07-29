import { useState } from "react";
import { ConfirmModal } from "../components/ConfirmModal";

/** The OCR "replace notes?" flow, shared by EvidencePanel's clue note and
 *  RegionModal's region note: OCR text lands straight in an empty note, but
 *  overwriting one that already has something needs a confirmation first —
 *  the extraction can't be undone once it clobbers what was there.
 *
 *  `message` stays owned by the caller (the two hosts' wording differs by a
 *  word), everything else is identical between them. */
export function useOcrReplaceConfirm(setText: (text: string) => void, message: string) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [pending, setPending] = useState<string | null>(null);

  const offerText = (existingText: string, newText: string) => {
    if (existingText.trim()) {
      setPending(newText);
      setConfirmOpen(true);
    } else {
      setText(newText);
    }
  };

  const confirmModal = (
    <ConfirmModal
      opened={confirmOpen}
      onClose={() => setConfirmOpen(false)}
      onConfirm={() => {
        if (pending !== null) setText(pending);
        setConfirmOpen(false);
      }}
      title="Replace existing note?"
      message={message}
      confirmLabel="Replace"
    />
  );

  return { offerText, confirmModal };
}
