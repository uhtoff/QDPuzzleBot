-- M8: link mini-puzzles to their minimeta parent
ALTER TABLE puzzles ADD COLUMN parent_puzzle_id INT DEFAULT NULL;
ALTER TABLE puzzles ADD CONSTRAINT fk_puzzle_parent
    FOREIGN KEY (parent_puzzle_id) REFERENCES puzzles(id) ON DELETE SET NULL;
