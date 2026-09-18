-- The direct-SQL insert path was parked (2026-09-18) in favor of the
-- Excel-import path -- see findings/SQL_PREFLIGHT_NOTES.md and README.md
-- for why (a single voucher touches 123 Tran1 + up to 5x104 Tran2 columns,
-- several with unconfirmed write-time semantics; the Excel-import path goes
-- through Busy's real save code instead of guessing at those).
--
-- busy_wms_writer is unused as a result. It's tightly scoped (INSERT-only
-- on Tran1/Tran2, verified) so leaving it is low-risk, but if you'd rather
-- remove it entirely, run this yourself the same way you ran the CREATE
-- script (SSMS or sqlcmd -E):

USE BusyCOMP0001_db12026;
GO
DROP USER IF EXISTS busy_wms_writer;
GO
DROP LOGIN busy_wms_writer;
GO
