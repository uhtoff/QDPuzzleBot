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
    <Modal opened={mediaId != null} onClose={onClose} fullScreen title="Mark up">
      {mediaId != null && <MarkupCanvas mediaId={mediaId} alt={alt} />}
    </Modal>
  );
}
