-- 037_message_attachment_fk.sql
-- T1 (docs/reviews/2026-08-07-comprehensive-review.md): message_attachments has no FK to
-- messages (027_message_bridge.sql declares neither), so MessageDb.delete_message has to
-- delete the attachment rows and the message row as two separate autocommitted statements.
-- A failure between them destroys the attachment rows while leaving the message. Add the
-- cascade so the code can delete one row and let the database guarantee the rest.
--
-- Existing orphan rows would violate the FK, so purge them first -- they are already
-- unreachable dead data (their owning message is gone). Both columns are already
-- BIGINT UNSIGNED, so no MODIFY is needed (unlike 022, which had to narrow a type first).

DELETE a FROM message_attachments a
  LEFT JOIN messages m ON a.message_id = m.id
  WHERE m.id IS NULL;

ALTER TABLE `message_attachments`
  ADD CONSTRAINT `MessageAttachmentMessageID` FOREIGN KEY (`message_id`)
      REFERENCES `messages` (`id`) ON DELETE CASCADE ON UPDATE CASCADE;
