-- Run this yourself (SSMS or `sqlcmd -E`) against DEV-MACHINE\SQLEXPRESS01.
-- Creates a write-capable login scoped to EXACTLY what the voucher-push
-- insert path needs, nothing more. No db_datawriter/db_datareader role
-- membership -- SQL Server's default-deny model means this login has ZERO
-- access to anything not explicitly granted below.
--
-- Replace <STRONG_PASSWORD_HERE> before running. Afterward, put the
-- credentials in busy-voucher-push/config.local.json (gitignored, same
-- pattern busy-extraction-poc/config.local.json already uses for
-- busy_sync_reader) -- never paste the password back into chat.

USE BusyCOMP0001_db12026;
GO

CREATE LOGIN busy_wms_writer WITH PASSWORD = '<STRONG_PASSWORD_HERE>', CHECK_POLICY = ON;
CREATE USER busy_wms_writer FOR LOGIN busy_wms_writer;
GO

-- Minimal footprint: SELECT+INSERT on Tran1 (voucher header, needed to read
-- MAX(AutoVchNo) under lock and then insert the new header row in the same
-- transaction), INSERT-only on Tran2 (voucher lines -- never needs to read
-- them). Nothing else is granted, so nothing else is reachable.
GRANT SELECT, INSERT ON dbo.Tran1 TO busy_wms_writer;
GRANT INSERT ON dbo.Tran2 TO busy_wms_writer;

-- Explicit belt-and-suspenders DENY on the two tables above, mirroring
-- busy_sync_reader's own pattern -- object-scoped (not database-scoped) so
-- it can never shadow the specific INSERT grants above (a database-scoped
-- DENY INSERT would override the object-level GRANT INSERT -- DENY always
-- wins regardless of scope, so this must stay object-level).
DENY UPDATE, DELETE ON dbo.Tran1 TO busy_wms_writer;
DENY SELECT, UPDATE, DELETE ON dbo.Tran2 TO busy_wms_writer;

-- Defense in depth against DDL, even though nothing above implies it.
DENY CREATE TABLE TO busy_wms_writer;
DENY CREATE VIEW TO busy_wms_writer;
DENY CREATE PROCEDURE TO busy_wms_writer;
DENY CREATE FUNCTION TO busy_wms_writer;
DENY ALTER ON SCHEMA::dbo TO busy_wms_writer;
GO

-- Verification -- run these too and share the results (no real data touched):
-- SELECT * FROM fn_my_permissions('dbo.Tran1', 'OBJECT') ORDER BY permission_name;
-- SELECT * FROM fn_my_permissions('dbo.Tran2', 'OBJECT') ORDER BY permission_name;
-- (run AS the new login, e.g. `sqlcmd -S ... -U busy_wms_writer -P ...`)
-- Expected: Tran1 shows SELECT+INSERT only; Tran2 shows INSERT only.
