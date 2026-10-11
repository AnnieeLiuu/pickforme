#!/usr/bin/env bash
# ==============================================================================
# 🍱 PickForMe - Google Cloud Run 自動化生產環境部署腳本
# ==============================================================================
# 功能說明:
#   1. 自動讀取 .env 中的金鑰與配置
#   2. 驗證 gcloud CLI 登入狀態與 GCP Project 設定
#   3. 使用 Google Cloud Build 建構 Docker 容器映像檔並推送至 Artifact Registry
#   4. 部署/更新至 Cloud Run 服務 (包含記憶體、CPU、並行量與環境變數設定)
#   5. (選用) 部署完成後自動設定 Telegram Webhook
#
# 使用方式:
#   bash scripts/deploy_cloud_run.sh [OPTIONS]
#
# 選項:
#   --dry-run          僅模擬檢查並印出即將執行的指令，不實際調用 GCP API
#   --setup-webhook    部署完成後自動呼叫 Telegram setWebhook 綁定 Cloud Run 服務網址
#   --region <REGION>  指定 GCP 區域 (預設: asia-northeast1 東京)
#   --project <ID>     指定 GCP Project ID (預設取自 gcloud config 或 .env GCP_PROJECT_ID)
#   --service <NAME>   指定 Cloud Run 服務名稱 (預設: pickforme-api)
# ==============================================================================

set -euo pipefail

# 終端機顏色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 預設參數
SERVICE_NAME="pickforme-api"
REGION="asia-northeast1"
PROJECT_ID=""
DRY_RUN=false
SETUP_WEBHOOK=false
MIN_INSTANCES=0
MAX_INSTANCES=5
MEMORY="1Gi"
CPU="1"
TIMEOUT="60s"
CONCURRENCY=80

# 解析命令列參數
while [[ $# -gt 0 ]]; do
  case $1 in
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --setup-webhook)
      SETUP_WEBHOOK=true
      shift
      ;;
    --region)
      REGION="$2"
      shift 2
      ;;
    --project)
      PROJECT_ID="$2"
      shift 2
      ;;
    --service)
      SERVICE_NAME="$2"
      shift 2
      ;;
    -h|--help)
      echo "用法: bash scripts/deploy_cloud_run.sh [--dry-run] [--setup-webhook] [--region <REGION>] [--project <ID>] [--service <NAME>]"
      exit 0
      ;;
    *)
      echo -e "${RED}❌ 未知參數: $1${NC}"
      exit 1
      ;;
  esac
done

echo -e "${BLUE}======================================================${NC}"
echo -e "${BLUE}🍱 PickForMe Cloud Run 自動化生產部署流程啟動${NC}"
echo -e "${BLUE}======================================================${NC}"

# 1. 載入 .env 檔案 (若存在)
ENV_FILE=".env"
if [ -f "$ENV_FILE" ]; then
    echo -e "${GREEN}📄 載入環境變數檔: ${ENV_FILE}${NC}"
    # 導出 .env 中的變數供腳本使用，忽略註解與空行
    set -a
    # shellcheck disable=SC1090
    source <(grep -E '^[A-Za-z0-9_]+=' "$ENV_FILE")
    set +a
else
    echo -e "${YELLOW}⚠️ 未找到 .env 檔案，將使用環境中現存變數${NC}"
fi

# 2. 檢查必要工具
if ! command -v gcloud &> /dev/null; then
    if [ "$DRY_RUN" = true ]; then
        echo -e "${YELLOW}⚠️ 提示: 本機未偵測到 gcloud CLI，但在 --dry-run 模擬模式下將繼續演繹指令流程${NC}"
    else
        echo -e "${RED}❌ 找不到 gcloud CLI 工具，請先安裝 Google Cloud SDK: https://cloud.google.com/sdk/docs/install${NC}"
        exit 1
    fi
fi

# 3. 確定 GCP 專案 ID
if [ -z "$PROJECT_ID" ]; then
    PROJECT_ID="${GCP_PROJECT_ID:-$(command -v gcloud &>/dev/null && gcloud config get-value project 2>/dev/null || echo "")}"
fi

if [ -z "$PROJECT_ID" ] || [ "$PROJECT_ID" == "(unset)" ]; then
    if [ "$DRY_RUN" = true ]; then
        PROJECT_ID="pickforme-gcp-project"
        echo -e "${YELLOW}⚠️ [DRY-RUN] 未指定 Project ID，採用模擬預設值: ${PROJECT_ID}${NC}"
    else
        echo -e "${RED}❌ 找不到有效的 GCP Project ID！請透過 --project 參數指定，或執行 'gcloud config set project <PROJECT_ID>'${NC}"
        exit 1
    fi
fi

echo -e "🔹 專案 ID (Project ID) : ${YELLOW}${PROJECT_ID}${NC}"
echo -e "🔹 部署區域 (Region)     : ${YELLOW}${REGION}${NC}"
echo -e "🔹 服務名稱 (Service)    : ${YELLOW}${SERVICE_NAME}${NC}"
echo -e "🔹 模擬模式 (Dry Run)    : ${YELLOW}${DRY_RUN}${NC}"

IMAGE_TAG="gcr.io/${PROJECT_ID}/${SERVICE_NAME}:latest"

# 4. 整理需注入 Cloud Run 的生產環境變數
ENV_VARS_LIST=()

# 必填或重要連線設定
[ -n "${DATABASE_URL:-}" ] && ENV_VARS_LIST+=("DATABASE_URL=${DATABASE_URL}")
[ -n "${TELEGRAM_BOT_TOKEN:-}" ] && ENV_VARS_LIST+=("TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}")
[ -n "${TELEGRAM_WEBHOOK_SECRET:-}" ] && ENV_VARS_LIST+=("TELEGRAM_WEBHOOK_SECRET=${TELEGRAM_WEBHOOK_SECRET}")
[ -n "${GOOGLE_MAPS_API_KEY:-}" ] && ENV_VARS_LIST+=("GOOGLE_MAPS_API_KEY=${GOOGLE_MAPS_API_KEY}")
[ -n "${GEMINI_API_KEY:-}" ] && ENV_VARS_LIST+=("GEMINI_API_KEY=${GEMINI_API_KEY}")
[ -n "${GROQ_API_KEY:-}" ] && ENV_VARS_LIST+=("GROQ_API_KEY=${GROQ_API_KEY}")
[ -n "${LLM_PRIMARY_PROVIDER:-}" ] && ENV_VARS_LIST+=("LLM_PRIMARY_PROVIDER=${LLM_PRIMARY_PROVIDER}")
[ -n "${TELEGRAM_ALLOWED_USERS:-}" ] && ENV_VARS_LIST+=("TELEGRAM_ALLOWED_USERS=${TELEGRAM_ALLOWED_USERS}")

# 合併為 gcloud 接受的逗號分隔字串
ENV_VARS_STR=""
if [ ${#ENV_VARS_LIST[@]} -gt 0 ]; then
    ENV_VARS_STR=$(IFS=,; echo "${ENV_VARS_LIST[*]}")
fi

echo -e "\n${BLUE}📦 步驟 1/3: 建構並推送容器映像檔...${NC}"
BUILD_CMD="gcloud builds submit --tag ${IMAGE_TAG} --project ${PROJECT_ID}"
echo -e "執行指令: ${YELLOW}${BUILD_CMD}${NC}"

if [ "$DRY_RUN" = false ]; then
    $BUILD_CMD
else
    echo -e "${YELLOW}[DRY-RUN] 略過容器映像檔建構${NC}"
fi

echo -e "\n${BLUE}🚀 步驟 2/3: 部署至 Google Cloud Run...${NC}"
DEPLOY_CMD="gcloud run deploy ${SERVICE_NAME} \
  --image ${IMAGE_TAG} \
  --platform managed \
  --region ${REGION} \
  --project ${PROJECT_ID} \
  --allow-unauthenticated \
  --memory ${MEMORY} \
  --cpu ${CPU} \
  --min-instances ${MIN_INSTANCES} \
  --max-instances ${MAX_INSTANCES} \
  --timeout ${TIMEOUT} \
  --concurrency ${CONCURRENCY} \
  --port 8080"

if [ -n "$ENV_VARS_STR" ]; then
    DEPLOY_CMD="${DEPLOY_CMD} --set-env-vars \"${ENV_VARS_STR}\""
fi

echo -e "執行指令: ${YELLOW}${DEPLOY_CMD}${NC}"

SERVICE_URL=""
if [ "$DRY_RUN" = false ]; then
    eval "$DEPLOY_CMD"
    SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" --platform managed --region "${REGION}" --project "${PROJECT_ID}" --format 'value(status.url)')
    echo -e "\n${GREEN}✅ Cloud Run 部署成功！服務公開網址: ${SERVICE_URL}${NC}"
else
    SERVICE_URL="https://${SERVICE_NAME}-${PROJECT_ID}.${REGION}.run.app"
    echo -e "${YELLOW}[DRY-RUN] 略過實際部署，模擬服務網址: ${SERVICE_URL}${NC}"
fi

echo -e "\n${BLUE}🔗 步驟 3/3: Webhook 綁定檢查...${NC}"
WEBHOOK_ENDPOINT="${SERVICE_URL}/api/telegram/webhook"

if [ "$SETUP_WEBHOOK" = true ]; then
    echo -e "即將將 Telegram Webhook 指向: ${YELLOW}${WEBHOOK_ENDPOINT}${NC}"
    if [ "$DRY_RUN" = false ]; then
        if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
            python scripts/setup_webhook.py --url "${WEBHOOK_ENDPOINT}"
            echo -e "${GREEN}✅ Telegram Webhook 綁定完成！${NC}"
        else
            echo -e "${RED}⚠️ 未設定 TELEGRAM_BOT_TOKEN，無法自動綁定 Webhook${NC}"
        fi
    else
        echo -e "${YELLOW}[DRY-RUN] 略過 Webhook 實際綁定調用${NC}"
    fi
else
    echo -e "💡 提示: 若要綁定 Telegram Webhook 至本次部署的網址，可執行："
    echo -e "   ${YELLOW}python scripts/setup_webhook.py --url \"${WEBHOOK_ENDPOINT}\"${NC}"
    echo -e "   或在部署時加上 ${YELLOW}--setup-webhook${NC} 參數。"
fi

echo -e "\n${GREEN}🎉 PickForMe 上雲 GCP Cloud Run 流程圓滿完成！${NC}"
