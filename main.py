import os
import json
import base64
import requests
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, db
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse

# ============================================
# CONFIGURAÇÕES (variáveis de ambiente)
# ============================================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
FIREBASE_CRED_JSON = os.environ.get("FIREBASE_CRED_JSON")
FIREBASE_URL = "https://qualidade-do-ar-tcc-default-rtdb.firebaseio.com/"

# ============================================
# INICIALIZAR FIREBASE
# ============================================

try:
    cred_dict = json.loads(FIREBASE_CRED_JSON)
    cred = credentials.Certificate(cred_dict)
    firebase_admin.initialize_app(cred, {'databaseURL': FIREBASE_URL})
    print("✅ Firebase conectado!")
except Exception as e:
    print(f"❌ Erro no Firebase: {e}")

# ============================================
# FUNÇÕES AUXILIARES
# ============================================

def ler_dados_firebase():
    try:
        ref = db.reference('/sensor')
        return ref.get()
    except Exception as e:
        print(f"❌ Erro ao ler Firebase: {e}")
        return None

def ler_grafico_firebase():
    try:
        ref = db.reference('/grafico_diario')
        dados = ref.get()
        if dados and 'imagem' in dados:
            return base64.b64decode(dados['imagem'])
        return None
    except Exception as e:
        print(f"❌ Erro ao ler gráfico: {e}")
        return None

def classificar_ar(pm25):
    if pm25 <= 15: return "🟢 BOA"
    elif pm25 <= 25: return "🟡 MODERADA"
    elif pm25 <= 50: return "🟠 RUIM"
    elif pm25 <= 100: return "🔴 MUITO RUIM"
    else: return "⚫ PÉSSIMA"

def gerar_relatorio(dados):
    if not dados:
        return "⚠️ Dados indisponíveis."
    
    temp = dados.get('temperatura', 0)
    umid = dados.get('umidade', 0)
    pressao = dados.get('pressao', 0)
    pm25 = dados.get('pm25', 0)
    pm10 = dados.get('pm10', 0)
    voc = dados.get('voc', 0)
    
    classificacao = classificar_ar(pm25)
    hora = datetime.now().strftime("%H:%M")
    data = datetime.now().strftime("%d/%m/%Y")
    
    return f"""📊 RELATÓRIO DE QUALIDADE DO AR
🕐 {hora} - {data}
━━━━━━━━━━━━━━━━━━━━━━
🌡️ Temperatura: {temp:.1f}°C
💧 Umidade: {umid:.0f}%
📊 Pressão: {pressao:.0f} hPa
━━━━━━━━━━━━━━━━━━━━━━
🧹 PM2.5: {pm25:.1f} µg/m³
🧹 PM10: {pm10:.1f} µg/m³
🧪 VOC: {voc}
━━━━━━━━━━━━━━━━━━━━━━
📊 Classificação: {classificacao}"""

def gerar_previsao(dados):
    if not dados:
        return "⚠️ Dados indisponíveis."
    
    temp = dados.get('temperatura', 0)
    umid = dados.get('umidade', 0)
    pressao = dados.get('pressao', 0)
    
    mensagem = f"""🌤️ PREVISÃO DO TEMPO
━━━━━━━━━━━━━━━━━━━━━━
🌡️ Temperatura: {temp:.1f}°C
💧 Umidade: {umid:.0f}%
📊 Pressão: {pressao:.0f} hPa
━━━━━━━━━━━━━━━━━━━━━━
📊 Análise:"""

    if temp > 30:
        mensagem += "\n☀️ Dia quente! Mantenha-se hidratado."
    elif temp < 18:
        mensagem += "\n❄️ Dia frio! Vista-se bem."
    else:
        mensagem += "\n🌤️ Temperatura agradável."
    
    if pressao < 960:
        mensagem += "\n🌧️ Pressão baixa - Possível chuva!"
    elif pressao > 975:
        mensagem += "\n☀️ Pressão alta - Tempo estável!"
    else:
        mensagem += "\n📊 Pressão normal."
    
    if umid > 70:
        mensagem += "\n💧 Umidade alta - Ar úmido."
    elif umid < 40:
        mensagem += "\n💨 Umidade baixa - Ar seco."
    else:
        mensagem += "\n💨 Umidade confortável."
    
    return mensagem

def gerar_alertas():
    try:
        ref = db.reference('/historico')
        historico = ref.get()
        
        if not historico:
            return "⚠️ Histórico indisponível."
        
        timestamps = sorted(historico.keys())
        if len(timestamps) < 6:
            return f"⏳ Coletando dados... ({len(timestamps)}/6)"
        
        dados_atual = ler_dados_firebase()
        if not dados_atual:
            return "⚠️ Dados atuais indisponíveis."
        
        idx_antigo = len(timestamps) - 6
        dados_antigo = historico[timestamps[idx_antigo]]
        
        var_temp = dados_atual.get('temperatura', 0) - dados_antigo.get('temperatura', 0)
        var_press = dados_atual.get('pressao', 0) - dados_antigo.get('pressao', 0)
        var_umid = dados_atual.get('umidade', 0) - dados_antigo.get('umidade', 0)
        
        alertas = "⚠️ ALERTAS METEOROLÓGICOS\n━━━━━━━━━━━━━━━━━━━━━━\n"
        tem_alerta = False
        
        if abs(var_temp) >= 2.0:
            tem_alerta = True
            alertas += f"🌡️ {'🔽 Queda' if var_temp < 0 else '🔥 Subida'} de {abs(var_temp):.1f}°C em 1h\n"
        
        if abs(var_press) >= 5.0:
            tem_alerta = True
            alertas += f"📊 {'⬇️ Queda' if var_press < 0 else '⬆️ Subida'} de {abs(var_press):.1f} hPa em 1h\n"
        
        if abs(var_umid) >= 15.0:
            tem_alerta = True
            alertas += f"💧 {'🔽 Queda' if var_umid < 0 else '🔼 Subida'} de {abs(var_umid):.1f}% em 1h\n"
        
        if not tem_alerta:
            alertas += "✅ TEMPO ESTÁVEL!\n━━━━━━━━━━━━━━━━━━━━━━\n"
            alertas += f"🌡️ {dados_atual.get('temperatura', 0):.1f}°C | 💧 {dados_atual.get('umidade', 0):.0f}%\n"
            alertas += f"📊 {dados_atual.get('pressao', 0):.0f} hPa\n"
            alertas += "Nenhum alerta meteorológico previsto."
        
        return alertas
        
    except Exception as e:
        return f"❌ Erro: {e}"

def enviar_grafico_telegram_privado(chat_id):
    """Envia o gráfico para o chat privado do usuário"""
    print(f"📊 Buscando gráfico no Firebase para {chat_id}...")
    imagem_bytes = ler_grafico_firebase()
    
    if not imagem_bytes:
        print("❌ Gráfico não encontrado no Firebase")
        return False
    
    print(f"✅ Gráfico encontrado! Tamanho: {len(imagem_bytes)} bytes")
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    files = {'photo': ('grafico.png', imagem_bytes, 'image/png')}
    data = {'chat_id': chat_id, 'caption': '📊 Relatório Diário - Qualidade do Ar'}
    
    try:
        response = requests.post(url, files=files, data=data)
        if response.status_code == 200:
            print(f"✅ Gráfico enviado para {chat_id}!")
            return True
        else:
            print(f"❌ Erro: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Erro: {e}")
        return False

# ============================================
# COMANDOS DO TELEGRAM - VERSÃO 2
# ============================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /start - Versão 2: botões apenas no grupo"""
    
    chat = update.effective_chat
    
    # 🔧 Se for PRIVADO, apenas confirma e não envia botões
    if chat.type == "private":
        await update.message.reply_text(
            "✅ Comando recebido!\n"
            "📌 Os botões estão disponíveis no grupo.\n"
            "   Clique em um botão lá para receber as informações aqui."
        )
        return
    
    # 🔧 Se for GRUPO, envia e fixa a mensagem
    keyboard = [
        [InlineKeyboardButton("📊 Relatório do Ar", callback_data="relatorio")],
        [InlineKeyboardButton("🌤️ Previsão do Tempo", callback_data="previsao")],
        [InlineKeyboardButton("⚠️ Alertas Meteorológicos", callback_data="alertas")],
        [InlineKeyboardButton("📈 Gráfico Diário", callback_data="grafico")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    mensagem = await update.message.reply_text(
        "🔹 SISTEMA DE QUALIDADE DO AR\n━━━━━━━━━━━━━━━━━━━━━━\n📊 Clique nos botões abaixo para receber as informações no seu privado:",
        reply_markup=reply_markup
    )
    
    # Fixa a mensagem no topo do grupo
    try:
        await chat.pin_message(mensagem.message_id)
        print("📌 Mensagem fixada no grupo!")
    except Exception as e:
        print(f"⚠️ Não foi possível fixar a mensagem: {e}")
        print("   → O bot precisa ser administrador do grupo para fixar mensagens.")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("✅ Processando...")
    
    # Pega o ID e nome do usuário que clicou
    user_id = query.from_user.id
    user_name = query.from_user.first_name or "Usuário"
    
    dados = ler_dados_firebase()
    
    # Gera a mensagem baseado no botão clicado
    if query.data == "relatorio":
        mensagem = gerar_relatorio(dados)
    elif query.data == "previsao":
        mensagem = gerar_previsao(dados)
    elif query.data == "alertas":
        mensagem = gerar_alertas()
    elif query.data == "grafico":
        sucesso = enviar_grafico_telegram_privado(user_id)
        if sucesso:
            mensagem = "📊 Gráfico enviado no seu privado!"
        else:
            mensagem = "⚠️ Gráfico indisponível no momento. Aguarde o relatório das 20h."
    else:
        mensagem = "⚠️ Comando não reconhecido!"
    
    # 🔧 ENVIA A RESPOSTA NO PRIVADO DO USUÁRIO
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=mensagem + "\n\n━━━━━━━━━━━━━━━━━━━━━━\n📊 Use /start no grupo para ver os botões novamente."
        )
        
        # Confirma no grupo sem editar a mensagem original
        await context.bot.send_message(
            chat_id=query.message.chat.id,
            text=f"✅ {user_name}, a resposta foi enviada no seu privado! 📩"
        )
        
        await query.answer("✅ Mensagem enviada no seu privado!")
        
    except Exception as e:
        print(f"❌ Erro ao enviar mensagem privada: {e}")
        # Fallback: se não conseguir enviar no privado, envia no grupo
        keyboard = [
            [InlineKeyboardButton("📊 Relatório do Ar", callback_data="relatorio")],
            [InlineKeyboardButton("🌤️ Previsão do Tempo", callback_data="previsao")],
            [InlineKeyboardButton("⚠️ Alertas Meteorológicos", callback_data="alertas")],
            [InlineKeyboardButton("📈 Gráfico Diário", callback_data="grafico")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            text=mensagem + "\n\n━━━━━━━━━━━━━━━━━━━━━━\n🔘 Clique nos botões:",
            reply_markup=reply_markup
        )

# ============================================
# WEBHOOK
# ============================================

app = Starlette()
bot_application = None
app_initialized = False

@app.route("/")
async def home(request):
    return JSONResponse({"status": "Bot is running!"})

@app.route("/health")
async def health(request):
    return JSONResponse({"status": "OK"})

@app.route("/webhook", methods=["POST"])
async def webhook(request):
    global app_initialized
    
    try:
        body = await request.json()
        print(f"📨 Webhook recebido: {body}")
        
        if not app_initialized and bot_application:
            await bot_application.initialize()
            app_initialized = True
            print("✅ Aplicação inicializada!")
        
        if bot_application:
            update = Update.de_json(body, bot_application.bot)
            await bot_application.process_update(update)
        
        return JSONResponse({"status": "ok"})
        
    except Exception as e:
        print(f"❌ Erro no webhook: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.route("/enviar_grafico")
async def enviar_grafico_endpoint(request):
    sucesso = enviar_grafico_telegram_privado(CHAT_ID)
    return JSONResponse({"status": "success" if sucesso else "error"})

# ============================================
# MAIN
# ============================================

def main():
    global bot_application, app_initialized
    
    print("🚀 Bot do Telegram (Render) iniciado!")
    print("📊 4 botões disponíveis:")
    print("   📊 Relatório do Ar")
    print("   🌤️ Previsão do Tempo")
    print("   ⚠️ Alertas Meteorológicos")
    print("   📈 Gráfico Diário")
    print("━━━━━━━━━━━━━━━━━━━━━━")
    print("📌 Versão 2: Botões apenas no grupo")
    print("   /start no privado → apenas confirmação")
    print("   /start no grupo → envia e fixa botões")
    print("━━━━━━━━━━━━━━━━━━━━━━")
    
    bot_application = Application.builder().token(TELEGRAM_TOKEN).build()
    bot_application.add_handler(CommandHandler("start", start))
    bot_application.add_handler(CallbackQueryHandler(button_callback))
    
    app_initialized = False
    
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")

if __name__ == "__main__":
    main()
