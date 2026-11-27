import { Modal } from "@mantine/core";
import type { PostTarget } from "../types";
import { MarkupCanvas } from "./MarkupCanvas";

type Props = {
  mediaId: number | null;
  alt: string;
  onClose: () => void;
  /** When set, the editor offers "Post to Discord" for this target. */
  postTarget?: PostTarget;
  /** Where the post lands, for the dialog (see postDestination). */
  postDestination?: string;
};

/** Full-screen wrapper so a marked-up image gets the whole window. Everything
 *  else lives in MarkupCanvas, which is reused by any surface that has a
 *  media id. */
export function MarkupModal({ mediaId, alt, onClose, postTarget, postDestination }: Props) {
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
        <MarkupCanvas mediaId={mediaId} alt={alt} onSave={onClose}
                      postTarget={postTarget} postDestination={postDestination} />
      )}
    </Modal>
  );
}
