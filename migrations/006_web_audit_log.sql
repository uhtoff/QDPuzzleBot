-- Audit log of dashboard (web) mutations: which human did what.
CREATE TABLE IF NOT EXISTS web_audit_log (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  user_id     BIGINT NOT NULL,
  username    VARCHAR(255),
  action      VARCHAR(64) NOT NULL,
  puzzle_id   INT NULL,
  hunt_uid    VARCHAR(64) NULL,
  detail      VARCHAR(255) NULL
);
