-- Backfill transactions table with historical add/drop data
-- Schema: bot_id, added (player_id), dropped (player_id), bid, week, date

-- Create the transactions table
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id INTEGER NOT NULL,
    added INTEGER NOT NULL,
    dropped INTEGER NOT NULL,
    bid INTEGER NOT NULL,
    week INTEGER NOT NULL,
    date TEXT NOT NULL
);

-- Week 3 (first run - 2025-09-18)
INSERT INTO transactions (bot_id, added, dropped, bid, week, date) VALUES
(11, 27166, 16483, 11, 3, '2025-09-18'),
(11, 25388, 22921, 5, 3, '2025-09-18'),
(4, 27165, 20095, 20, 3, '2025-09-18'),
(4, 23107, 25361, 20, 3, '2025-09-18'),
(4, 23018, 11177, 15, 3, '2025-09-18'),
(4, 8030, 8160, 15, 3, '2025-09-18'),
(4, 16910, 11465, 10, 3, '2025-09-18');

-- Week 3 (second run - 2025-09-17)
INSERT INTO transactions (bot_id, added, dropped, bid, week, date) VALUES
(0, 8110, 8150, 0, 3, '2025-09-17'),
(0, 11616, 16406, 10, 3, '2025-09-17'),
(1, 8010, 8030, 0, 3, '2025-09-17'),
(1, 9001, 23018, 10, 3, '2025-09-17'),
(6, 18232, 23107, 30, 3, '2025-09-17'),
(6, 25391, 22718, 15, 3, '2025-09-17'),
(6, 27297, 27165, 10, 3, '2025-09-17');

-- Week 4 (2025-09-25)
INSERT INTO transactions (bot_id, added, dropped, bid, week, date) VALUES
(0, 23123, 26434, 12, 4, '2025-09-25'),
(6, 25337, 17258, 7, 4, '2025-09-25'),
(6, 15561, 19196, 3, 4, '2025-09-25'),
(6, 27534, 15756, 0, 4, '2025-09-25'),
(4, 11687, 16398, 1, 4, '2025-09-25'),
(4, 22718, 27165, 1, 4, '2025-09-25'),
(4, 23891, 26148, 1, 4, '2025-09-25'),
(4, 24172, 24360, 1, 4, '2025-09-25'),
(4, 22985, 23101, 1, 4, '2025-09-25'),
(4, 8140, 8030, 1, 4, '2025-09-25');

-- Week 5 (2025-10-01)
INSERT INTO transactions (bot_id, added, dropped, bid, week, date) VALUES
(5, 26434, 25247, 5, 5, '2025-10-01'),
(0, 14003, 13029, 2, 5, '2025-10-01'),
(0, 8130, 8110, 2, 5, '2025-10-01'),
(0, 9451, 23000, 5, 5, '2025-10-01'),
(4, 23794, 19398, 1, 5, '2025-10-01'),
(4, 18226, 23107, 0, 5, '2025-10-01'),
(4, 22913, 26019, 0, 5, '2025-10-01'),
(4, 23310, 24172, 0, 5, '2025-10-01'),
(4, 18256, 23891, 0, 5, '2025-10-01'),
(4, 19562, 22985, 0, 5, '2025-10-01'),
(4, 23081, 11687, 0, 5, '2025-10-01');

-- Week 6 (2025-10-08)
INSERT INTO transactions (bot_id, added, dropped, bid, week, date) VALUES
(4, 17236, 23018, 0, 6, '2025-10-08'),
(4, 25247, 19562, 0, 6, '2025-10-08'),
(4, 19647, 18256, 0, 6, '2025-10-08'),
(4, 15756, 16910, 0, 6, '2025-10-08');

-- Week 7 (2025-10-15)
INSERT INTO transactions (bot_id, added, dropped, bid, week, date) VALUES
(8, 11687, 19590, 10, 7, '2025-10-15');

-- Week 8 (2025-10-22) - No transactions

-- Week 9 (2025-10-30)
INSERT INTO transactions (bot_id, added, dropped, bid, week, date) VALUES
(8, 23000, 12119, 1, 9, '2025-10-30'),
(8, 13029, 19058, 1, 9, '2025-10-30');

-- Week 10 (2025-11-05)
INSERT INTO transactions (bot_id, added, dropped, bid, week, date) VALUES
(11, 22985, 15802, 4, 10, '2025-11-05'),
(11, 27520, 27166, 3, 10, '2025-11-05'),
(11, 23092, 19201, 3, 10, '2025-11-05');

-- Week 11 (2025-11-12)
INSERT INTO transactions (bot_id, added, dropped, bid, week, date) VALUES
(8, 15802, 20111, 1, 11, '2025-11-12');

-- Week 12 (2025-11-19)
INSERT INTO transactions (bot_id, added, dropped, bid, week, date) VALUES
(8, 12119, 15802, 1, 12, '2025-11-19'),
(8, 27165, 19245, 1, 12, '2025-11-19');

-- Week 13 (2025-11-26)
INSERT INTO transactions (bot_id, added, dropped, bid, week, date) VALUES
(0, 20111, 23072, 2, 13, '2025-11-26'),
(0, 19245, 23123, 2, 13, '2025-11-26'),
(8, 19201, 23000, 1, 13, '2025-11-26'),
(8, 19590, 25409, 1, 13, '2025-11-26'),
(8, 23791, 12119, 1, 13, '2025-11-26'),
(8, 23891, 27165, 1, 13, '2025-11-26');

-- Week 14 (2025-12-03)
INSERT INTO transactions (bot_id, added, dropped, bid, week, date) VALUES
(8, 23000, 19590, 1, 14, '2025-12-03');
