import { Modal } from "@mantine/core";
import { MarkupCanvas } from "./MarkupCanvas";

type Props = {
  mediaId: number | null;
  alt: string;
  onClose: () => void;
};

/** Full-screen wrapper so a marked-up image gets the whole window. Everything
 *  else lives in MarkupCanvas, which is reused by any surface that has a
 *  media id. */
export function MarkupModal({ mediaId, alt, onClose }: Props) {
  return (
    <Modal
      opened={mediaId != null}
      onClose={onClose}
      fullScreen
      title="Mark up"
      // The canvas sizes itself to whatever the body gives it, so the body has
      // to be a full-height flex column rather than shrink-wrapping its child —
      // otherwise the frame inside has no definite height to fill.
      styles={{
        content: { display: "flex", flexDirection: "column" },
        body: { flex: 1, minHeight: 0, display: "flex", flexDirection: "column" },
      }}
    >
      {mediaId != null && (
        <MarkupCanvas mediaId={mediaId} alt={alt} onSave={onClose} />
      )}
    </Modal>
  );
}
