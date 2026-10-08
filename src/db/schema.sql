-- 🍱 PickForMe Database DDL Schema (Neon Postgres)

-- 延伸模組：UUID 生成器
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 1. 餐廳基礎資料（僅永久保存 google_place_id；名稱/座標採用 Tabelog 資料以遵從 Google 條款）
CREATE TABLE IF NOT EXISTS restaurants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    google_place_id VARCHAR(255) UNIQUE NOT NULL,
    tabelog_url VARCHAR(500) UNIQUE,
    name_ja VARCHAR(255) NOT NULL,
    name_en VARCHAR(255),
    phone VARCHAR(50),
    lat DOUBLE PRECISION NOT NULL,
    lng DOUBLE PRECISION NOT NULL,
    address TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 2. Tabelog 分數與詳細快取資料
CREATE TABLE IF NOT EXISTS tabelog_cache (
    restaurant_id UUID PRIMARY KEY REFERENCES restaurants(id) ON DELETE CASCADE,
    tabelog_rating NUMERIC(3,2),
    review_count INT,
    price_lunch VARCHAR(50),
    price_dinner VARCHAR(50),
    match_confidence NUMERIC(3,2),
    match_method VARCHAR(50),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 3. 對話 Session 與記憶 (Telegram)
CREATE TABLE IF NOT EXISTS chat_sessions (
    chat_id BIGINT PRIMARY KEY,
    last_location_lat DOUBLE PRECISION,
    last_location_lng DOUBLE PRECISION,
    last_recommended_ids JSONB,
    conversation_history JSONB,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 4. 預熱工作佇列 (SKIP LOCKED)
CREATE TABLE IF NOT EXISTS prewarm_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    area_name VARCHAR(100) NOT NULL,
    status VARCHAR(20) DEFAULT 'pending',
    target_count INT DEFAULT 20,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 5. Agent Trace (Runs 總表)
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chat_id BIGINT,
    user_query TEXT,
    model_used VARCHAR(50),
    total_tokens INT,
    latency_ms INT,
    status VARCHAR(20),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 6. Agent Trace (Steps 步驟表)
CREATE TABLE IF NOT EXISTS agent_steps (
    step_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    step_number INT,
    action_type VARCHAR(50),
    tool_name VARCHAR(50),
    tool_input JSONB,
    tool_output JSONB,
    latency_ms INT,
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 索引建置
CREATE INDEX IF NOT EXISTS idx_restaurants_google_id ON restaurants(google_place_id);
CREATE INDEX IF NOT EXISTS idx_restaurants_phone ON restaurants(phone);
CREATE INDEX IF NOT EXISTS idx_prewarm_status ON prewarm_jobs(status);
CREATE INDEX IF NOT EXISTS idx_agent_steps_run_id ON agent_steps(run_id);
