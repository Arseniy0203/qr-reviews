-- Separate database for pytest, so tests never touch dev data.
-- Runs only on the first start of an empty Postgres volume.
CREATE DATABASE qr_reviews_test;
