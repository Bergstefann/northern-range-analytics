-- Northern Range Port Analytics -- Azure SQL schema.
-- Mirrors docs/data-project-build-spec.md section 4. Idempotent: every
-- CREATE is guarded so re-running this script on an already-provisioned
-- database is a no-op.

IF OBJECT_ID('dbo.ports', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.ports (
        port_id              INT IDENTITY(1,1) PRIMARY KEY,
        port_name            NVARCHAR(100)  NOT NULL,
        country_code         CHAR(2)        NOT NULL,
        un_locode            CHAR(5)        NULL,
        eurostat_code        NVARCHAR(20)   NOT NULL,
        merged_into_port_id  INT            NULL,
        CONSTRAINT UQ_ports_eurostat_code UNIQUE (eurostat_code),
        CONSTRAINT FK_ports_merged_into FOREIGN KEY (merged_into_port_id)
            REFERENCES dbo.ports (port_id)
    );
END;

IF OBJECT_ID('dbo.cargo_types', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.cargo_types (
        cargo_type_id    INT IDENTITY(1,1) PRIMARY KEY,
        cargo_type_name  NVARCHAR(50)  NOT NULL,
        cargo_type_code  NVARCHAR(20)  NOT NULL,
        CONSTRAINT UQ_cargo_types_code UNIQUE (cargo_type_code)
    );
END;

IF OBJECT_ID('dbo.port_throughput', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.port_throughput (
        throughput_id        INT IDENTITY(1,1) PRIMARY KEY,
        port_id               INT             NOT NULL,
        cargo_type_id          INT             NOT NULL,
        year                     SMALLINT        NOT NULL,
        direction                 NVARCHAR(10)    NOT NULL,
        gross_weight_tonnes         DECIMAL(18,2)   NOT NULL,
        source                        NVARCHAR(50)    NOT NULL,
        ingested_at                     DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME(),
        CONSTRAINT FK_throughput_port FOREIGN KEY (port_id)
            REFERENCES dbo.ports (port_id),
        CONSTRAINT FK_throughput_cargo_type FOREIGN KEY (cargo_type_id)
            REFERENCES dbo.cargo_types (cargo_type_id),
        -- Natural key: what makes a re-run idempotent (Phase 3 loader
        -- MERGEs on this instead of duplicating rows).
        CONSTRAINT UQ_throughput_natural_key UNIQUE
            (port_id, cargo_type_id, year, direction, source)
    );
END;

IF OBJECT_ID('dbo.data_quality_flags', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.data_quality_flags (
        flag_id        INT IDENTITY(1,1) PRIMARY KEY,
        throughput_id   INT             NULL,
        port_id          INT             NULL,
        flag_type         NVARCHAR(30)    NOT NULL,
        description         NVARCHAR(1000)  NOT NULL,
        resolution           NVARCHAR(1000)  NOT NULL,
        created_at             DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME(),
        CONSTRAINT FK_flags_throughput FOREIGN KEY (throughput_id)
            REFERENCES dbo.port_throughput (throughput_id),
        CONSTRAINT FK_flags_port FOREIGN KEY (port_id)
            REFERENCES dbo.ports (port_id)
    );
END;
