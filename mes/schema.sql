-- CNC 2130 MES - ma'lumotlar bazasi sxemasi
-- TZ 16-bo'lim asosida. PostgreSQL.

CREATE TABLE machines (
    id          SERIAL PRIMARY KEY,
    code        VARCHAR(32) UNIQUE NOT NULL,     -- 'cnc-2130-01'
    name        VARCHAR(128) NOT NULL,
    status      VARCHAR(16) NOT NULL DEFAULT 'active',
    created_at  TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE operators (
    id          SERIAL PRIMARY KEY,
    code        VARCHAR(32) UNIQUE NOT NULL,     -- 'OP-1142'
    name        VARCHAR(128) NOT NULL,
    role        VARCHAR(16) NOT NULL DEFAULT 'operator',  -- operator | admin
    active      BOOLEAN NOT NULL DEFAULT true
);

-- TZ 14: har bir stanok uchun sozlamalar
CREATE TABLE machine_config (
    machine_id              INT PRIMARY KEY REFERENCES machines(id),
    t1                      INT NOT NULL DEFAULT 900,
    t2                      INT NOT NULL DEFAULT 900,
    t_reason                INT NOT NULL DEFAULT 900,
    t_merge                 INT NOT NULL DEFAULT 30,
    t_relay                 INT NOT NULL DEFAULT 30,  -- rele NO->NC ushlab turish vaqti
    auto_shutdown_enabled   BOOLEAN NOT NULL DEFAULT true,
    updated_by              INT REFERENCES operators(id),
    updated_at              TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE shifts (
    id          SERIAL PRIMARY KEY,
    machine_id  INT NOT NULL REFERENCES machines(id),
    operator_id INT NOT NULL REFERENCES operators(id),
    shift_no    SMALLINT NOT NULL,               -- 1 yoki 2
    started_at  TIMESTAMP NOT NULL,
    ended_at    TIMESTAMP,
    status      VARCHAR(16) NOT NULL DEFAULT 'open'
                -- open | closed | blocked
);

CREATE TABLE jobs (
    id            SERIAL PRIMARY KEY,
    code          VARCHAR(64) UNIQUE NOT NULL,   -- 'ORD-2026-0417'
    name          VARCHAR(255),
    planned_qty   INT,
    status        VARCHAR(16) NOT NULL DEFAULT 'planned',
    created_at    TIMESTAMP NOT NULL DEFAULT now()
);

-- TZ 11: bitta stanokda bitta ishning bajarilish davri
CREATE TABLE job_runs (
    id              SERIAL PRIMARY KEY,
    job_id          INT NOT NULL REFERENCES jobs(id),
    machine_id      INT NOT NULL REFERENCES machines(id),
    shift_id        INT REFERENCES shifts(id),
    started_by      INT REFERENCES operators(id),  -- brak javobgarligi (TZ 11.2)
    finished_by     INT REFERENCES operators(id),
    responsible_id  INT REFERENCES operators(id),  -- NULL = stanokka yoziladi
    started_at      TIMESTAMP NOT NULL,
    ended_at        TIMESTAMP,
    work_sec        INT NOT NULL DEFAULT 0,
    idle_sec        INT NOT NULL DEFAULT 0,
    qty_ok          INT NOT NULL DEFAULT 0,
    qty_scrap       INT NOT NULL DEFAULT 0,
    status          VARCHAR(16) NOT NULL DEFAULT 'running'
                    -- running | done | unassigned
);

-- TZ 7.5: sabablar klassifikatori
CREATE TABLE reason_codes (
    code        VARCHAR(32) PRIMARY KEY,
    name_uz     VARCHAR(128) NOT NULL,
    category    VARCHAR(32) NOT NULL,
    charge_to   VARCHAR(32) NOT NULL,   -- machine | production | external | planned
    active      BOOLEAN NOT NULL DEFAULT true
);

INSERT INTO reason_codes (code, name_uz, category, charge_to) VALUES
    ('TOOL_BREAK',  'Asbob singan',           'breakdown', 'machine'),
    ('NO_MATERIAL', 'Material tugagan',       'supply',    'production'),
    ('POWER_OUT',   'Elektr uzilgan',         'external',  'external'),
    ('PROGRAM_ERR', 'Dastur xatosi',          'process',   'production'),
    ('BREAKDOWN',   'Stanok buzilgan',        'breakdown', 'machine'),
    ('REPAIR',      'Ta''mirlash',            'breakdown', 'machine'),
    ('LUNCH',       'Tushlik',                'planned',   'planned'),
    ('HANDOVER',    'Smena topshirish',       'planned',   'planned'),
    ('SETUP',       'Dastur tayyorlash',      'planned',   'planned'),
    ('OTHER',       'Boshqa',                 'other',     'production');

-- TZ 6: Pico dan keladigan hodisalar (kompyuterdagi MES sahifasi orqali, micro USB)
-- Pico da soat moduli yo'q. Kompyuter o'chiq paytda Pico toki ham uzilgan
-- bo'lsa, hodisaning kun vaqti noma'lum: started_at/ended_at NULL, faqat
-- duration_sec keladi. Bunday hodisa qabul qilingan kun ichida hisoblanadi.
CREATE TABLE events (
    id              SERIAL PRIMARY KEY,
    external_id     VARCHAR(64) UNIQUE NOT NULL,  -- 'cnc213001/000001472'
    machine_id      INT NOT NULL REFERENCES machines(id),
    seq             INT NOT NULL,                 -- Pico dagi tartib raqami
    job_run_id      INT REFERENCES job_runs(id),
    type            VARCHAR(24) NOT NULL,         -- POWER_ON: stanok yoqiq turgan davr
    started_at      TIMESTAMP,                    -- NULL = kun vaqti noma'lum
    ended_at        TIMESTAMP,
    event_day       DATE NOT NULL,                -- ended_at kuni, NULL bo'lsa uploaded_at kuni
    duration_sec    INT,
    extra           JSONB,                        -- run_sec, stop_type, limit_sec, cause ...
    reason_required BOOLEAN NOT NULL DEFAULT false,
    reason_code     VARCHAR(32) REFERENCES reason_codes(code),
    reason_text     TEXT,
    closed_by       INT REFERENCES operators(id),
    closed_at       TIMESTAMP,
    time_uncertain  BOOLEAN NOT NULL DEFAULT false,
    uploaded_at     TIMESTAMP NOT NULL DEFAULT now()
);

-- TZ 8: rejalashtirilgan to'xtashlar
CREATE TABLE planned_stops (
    id          SERIAL PRIMARY KEY,
    machine_id  INT NOT NULL REFERENCES machines(id),
    shift_id    INT REFERENCES shifts(id),
    type        VARCHAR(32) NOT NULL REFERENCES reason_codes(code),
    planned_sec INT NOT NULL,
    started_at  TIMESTAMP NOT NULL,
    ended_at    TIMESTAMP
);

-- TZ 10.2: ikki tomonlama tasdiq bilan ish topshirish
CREATE TABLE handovers (
    id                  SERIAL PRIMARY KEY,
    shift_from_id       INT REFERENCES shifts(id),
    shift_to_id         INT REFERENCES shifts(id),
    job_run_id          INT REFERENCES job_runs(id),
    from_operator_id    INT REFERENCES operators(id),
    to_operator_id      INT REFERENCES operators(id),
    from_confirmed_at   TIMESTAMP,
    to_confirmed_at     TIMESTAMP,
    accepted            BOOLEAN,
    admin_override_by   INT REFERENCES operators(id),  -- TZ 10.4
    note                TEXT
);

CREATE TABLE audit_log (
    id          SERIAL PRIMARY KEY,
    entity      VARCHAR(32) NOT NULL,
    entity_id   INT,
    action      VARCHAR(32) NOT NULL,
    user_id     INT REFERENCES operators(id),
    old_value   JSONB,
    new_value   JSONB,
    created_at  TIMESTAMP NOT NULL DEFAULT now()
);

-- Indekslar
CREATE INDEX idx_events_machine_time ON events (machine_id, started_at);
CREATE INDEX idx_events_machine_day ON events (machine_id, event_day);
CREATE UNIQUE INDEX idx_events_machine_seq ON events (machine_id, seq);
CREATE INDEX idx_events_open ON events (reason_required, closed_at)
    WHERE reason_required = true AND closed_at IS NULL;
CREATE INDEX idx_job_runs_machine ON job_runs (machine_id, started_at);
CREATE INDEX idx_shifts_machine ON shifts (machine_id, started_at);

-- TZ 7.1: FIFO navbat - eng eski yopilmagan hodisa.
-- Vaqti noma'lum hodisalar ham bor, shuning uchun Pico tartibi (seq) bo'yicha.
CREATE VIEW pending_reasons AS
SELECT e.*, m.code AS machine_code
FROM events e
JOIN machines m ON m.id = e.machine_id
WHERE e.reason_required = true AND e.closed_at IS NULL
ORDER BY e.machine_id, e.seq ASC;

-- Oylik hisobot: stanok har kuni qancha yoqiq turdi, shundan shpindel qancha aylandi
CREATE VIEW machine_on_time_daily AS
SELECT machine_id,
       event_day,
       SUM(duration_sec)                         AS on_sec,
       SUM(COALESCE((extra->>'run_sec')::INT, 0)) AS run_sec
FROM events
WHERE type = 'POWER_ON'
GROUP BY machine_id, event_day;
