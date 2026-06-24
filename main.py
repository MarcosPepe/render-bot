import os
import json
import base64
import requests
from datetime import datetime, timedelta
import firebase_admin
from firebase_admin import credentials, db
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse
import asyncio

# ============================================
# CONFIGURAÇÕES
# ============================================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
FIREBASE_CRED_JSON = os.environ.get("FIREBASE_CRED_JSON")
FIREBASE_URL = "https://qualidade-do-ar-tcc-default-rtdb.firebaseio.com/"

# Horários dos relatórios
HORARIOS_REPORT = ["07:00", "12:00", "15:00", "19:00"]
HORA_GRAFICO = "20:00"

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

def buscar_historico_dia():
    """Busca os últimos registros do histórico para o gráfico"""
    try:
        ref = db.reference('/historico')
        historico = ref.get()
        
        if not historico:
            return None
        
        timestamps = sorted(historico.keys())
        num_registros = min(100, len(timestamps))
        ultimos_timestamps = timestamps[-num_registros:]
        
        dados_filtrados = []
        for key in ultimos_timestamps:
            value = historico[key]
            dados_filtrados.append({
                'timestamp': int(key),
                'temp': value.get('temperatura', 0),
                'umid': value.get('umidade', 0),
                'pressao': value.get('pressao', 0),
                'pm25': value.get('pm25', 0),
                'pm10': value.get('pm10', 0),
                'voc': value.get('voc', 0)
            })
        
        return dados_filtrados
        
    except Exception as e:
        print(f"❌ Erro ao buscar histórico: {e}")
        return None

def classificar_ar(pm25):
    if pm25 <= 15: return "🟢 BOA"
    elif pm25 <= 25: return "🟡 MODERADA"
    elif pm25 <= 50: return "🟠 RUIM"
    elif pm25 <= 100: return "🔴 MUITO RUIM"
    else: return "⚫ PÉSSIMA"

def get_emoji_classificacao(pm25):
    if pm25 <= 15: return "🟢"
    elif pm25 <= 25: return "🟡"
    elif pm25 <= 50: return "🟠"
    elif pm25 <= 100: return "🔴"
    else: return "⚫"

# ============================================
# FUNÇÃO: GERAR RELATÓRIO EM TEXTO
# ============================================

def gerar_relatorio_tempo_real(dados):
    if not dados:
        return "⚠️ Dados indisponíveis no momento."
    
    temp = dados.get('temperatura', 0)
    umid = dados.get('umidade', 0)
    pressao = dados.get('pressao', 0)
    pm25 = dados.get('pm25', 0)
    pm10 = dados.get('pm10', 0)
    voc = dados.get('voc', 0)
    
    classificacao = classificar_ar(pm25)
    emoji = get_emoji_classificacao(pm25)
    hora = datetime.now().strftime("%H:%M")
    data = datetime.now().strftime("%d/%m/%Y")
    
    # Busca tendência
    try:
        ref = db.reference('/historico')
        historico = ref.get()
        if historico:
            timestamps = sorted(historico.keys())
            if len(timestamps) >= 2:
                ultimo = historico[timestamps[-1]]
                pm25_anterior = ultimo.get('pm25', pm25)
                if pm25 > pm25_anterior:
                    tendencia = "📈 Subindo"
                elif pm25 < pm25_anterior:
                    tendencia = "📉 Descendo"
                else:
                    tendencia = "➡️ Estável"
            else:
                tendencia = "➡️ Sem histórico"
        else:
            tendencia = "➡️ Sem histórico"
    except:
        tendencia = "➡️ Sem histórico"
    
    return f"""📊 RELATÓRIO EM TEMPO REAL
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
📊 Classificação: {emoji} {classificacao}
📈 Tendência: {tendencia}"""

# ============================================
# FUNÇÃO: GERAR GRÁFICO (USANDO MATPLOTLIB)
# ============================================

def gerar_grafico_diario(dados):
    """Gera gráfico com 4 painéis usando matplotlib"""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import io
        
        if not dados or len(dados) < 2:
            return None, "Dados insuficientes para gerar gráfico."
        
        # Prepara os dados
        indices = list(range(len(dados)))
        horas = [datetime.fromtimestamp(d['timestamp']).strftime('%H:%M') for d in dados]
        pm25 = [d['pm25'] for d in dados]
        pm10 = [d['pm10'] for d in dados]
        temp = [d['temp'] for d in dados]
        umid = [d['umid'] for d in dados]
        
        # Calcula estatísticas
        media_pm25 = sum(pm25) / len(pm25) if pm25 else 0
        max_pm25 = max(pm25) if pm25 else 0
        max_pm25_hora = horas[pm25.index(max_pm25)] if pm25 else "--:--"
        min_pm25 = min(pm25) if pm25 else 0
        classificacao = classificar_ar(media_pm25)
        emoji = get_emoji_classificacao(media_pm25)
        
        # Cria o gráfico com 4 painéis
        fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(14, 10))
        
        # PM2.5
        ax1.bar(indices, pm25, color='#1f77b4', alpha=0.7, width=0.6, label='PM2.5')
        ax1.plot(indices, pm25, 'o-', color='darkblue', linewidth=1.5, markersize=3, label='Tendência')
        ax1.axhline(y=media_pm25, color='red', linestyle='--', alpha=0.5, label=f'Média: {media_pm25:.1f}')
        ax1.axhline(y=25, color='orange', linestyle=':', alpha=0.7, label='Limite (25)')
        ax1.set_xlabel('Medições (últimas 24h)', fontsize=10)
        ax1.set_ylabel('PM2.5 (µg/m³)', fontsize=10)
        ax1.set_title(f'PM2.5 - Média: {media_pm25:.1f} | Max: {max_pm25:.1f} ({max_pm25_hora})', fontsize=11)
        ax1.legend(fontsize=8, loc='upper right')
        ax1.grid(True, alpha=0.3)
        ax1.set_xticks(indices[::max(1, len(indices)//10)])
        ax1.set_xticklabels(horas[::max(1, len(indices)//10)], rotation=45, ha='right', fontsize=7)
        
        # PM10
        ax2.bar(indices, pm10, color='#ff7f0e', alpha=0.7, width=0.6, label='PM10')
        ax2.plot(indices, pm10, 's-', color='darkred', linewidth=1.5, markersize=3, label='Tendência')
        media_pm10 = sum(pm10)/len(pm10) if pm10 else 0
        ax2.axhline(y=media_pm10, color='red', linestyle='--', alpha=0.5, label=f'Média: {media_pm10:.1f}')
        ax2.set_xlabel('Medições (últimas 24h)', fontsize=10)
        ax2.set_ylabel('PM10 (µg/m³)', fontsize=10)
        ax2.set_title(f'PM10 - Média: {media_pm10:.1f}', fontsize=11)
        ax2.legend(fontsize=8, loc='upper right')
        ax2.grid(True, alpha=0.3)
        ax2.set_xticks(indices[::max(1, len(indices)//10)])
        ax2.set_xticklabels(horas[::max(1, len(indices)//10)], rotation=45, ha='right', fontsize=7)
        
        # Temperatura
        ax3.plot(indices, temp, 'o-', color='#2ca02c', linewidth=2, markersize=4, label='Temperatura')
        ax3.fill_between(indices, temp, alpha=0.2, color='#2ca02c')
        media_temp = sum(temp)/len(temp) if temp else 0
        ax3.axhline(y=media_temp, color='red', linestyle='--', alpha=0.5, label=f'Média: {media_temp:.1f}°C')
        ax3.set_xlabel('Medições (últimas 24h)', fontsize=10)
        ax3.set_ylabel('Temperatura (°C)', fontsize=10)
        ax3.set_title(f'Temperatura - Média: {media_temp:.1f}°C', fontsize=11)
        ax3.legend(fontsize=8, loc='upper right')
        ax3.grid(True, alpha=0.3)
        ax3.set_xticks(indices[::max(1, len(indices)//10)])
        ax3.set_xticklabels(horas[::max(1, len(indices)//10)], rotation=45, ha='right', fontsize=7)
        
        # Umidade
        ax4.plot(indices, umid, 's-', color='#9467bd', linewidth=2, markersize=4, label='Umidade')
        ax4.fill_between(indices, umid, alpha=0.2, color='#9467bd')
        media_umid = sum(umid)/len(umid) if umid else 0
        ax4.axhline(y=media_umid, color='red', linestyle='--', alpha=0.5, label=f'Média: {media_umid:.0f}%')
        ax4.set_xlabel('Medições (últimas 24h)', fontsize=10)
        ax4.set_ylabel('Umidade (%)', fontsize=10)
        ax4.set_title(f'Umidade - Média: {media_umid:.0f}%', fontsize=11)
        ax4.legend(fontsize=8, loc='upper right')
        ax4.grid(True, alpha=0.3)
        ax4.set_xticks(indices[::max(1, len(indices)//10)])
        ax4.set_xticklabels(horas[::max(1, len(indices)//10)], rotation=45, ha='right', fontsize=7)
        
        # Título geral
        data_str = datetime.now().strftime('%d/%m/%Y')
        fig.suptitle(f'Qualidade do Ar - {data_str} | Classificação: {classificacao}', fontsize=14, fontweight='bold')
        
        plt.tight_layout()
        
        # Salva em memória
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=120, bbox_inches='tight')
        buf.seek(0)
        plt.close()
        
        # Gera relatório em texto
        relatorio = f"""📈 RELATÓRIO DIÁRIO COMPLETO
🕐 {datetime.now().strftime('%H:%M')} - {data_str}
━━━━━━━━━━━━━━━━━━━━━━
📊 Estatísticas do dia:
   Média PM2.5: {media_pm25:.1f} µg/m³
   Máximo PM2.5: {max_pm25:.1f} µg/m³ ({max_pm25_hora})
   Mínimo PM2.5: {min_pm25:.1f} µg/m³
   Classificação: {emoji} {classificacao}

🌡️ Condições finais:
   Temperatura: {dados[-1]['temp']:.1f}°C
   Umidade: {dados[-1]['umid']:.0f}%
   Pressão: {dados[-1]['pressao']:.0f} hPa
   VOC: {dados[-1]['voc']}"""
        
        return buf, relatorio
        
    except ImportError:
        print("⚠️ Matplotlib não instalado. Gráfico não gerado.")
        return None, "Biblioteca matplotlib não disponível."
    except Exception as e:
        print(f"❌ Erro ao gerar gráfico: {e}")
        return None, f"Erro ao gerar gráfico: {e}"

# ============================================
# FUNÇÃO: SALVAR GRÁFICO NO FIREBASE
# ============================================

def salvar_grafico_firebase(imagem_buffer, relatorio):
    """Salva o gráfico no Firebase para o botão 'Gráfico Diário'"""
    try:
        import base64
        imagem_buffer.seek(0)
        imagem_base64 = base64.b64encode(imagem_buffer.getvalue()).decode('utf-8')
        
        ref = db.reference('/grafico_diario')
        ref.set({
            'imagem': imagem_base64,
            'data': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'timestamp': int(datetime.now().timestamp())
        })
        print("✅ Gráfico salvo no Firebase!")
        return True
    except Exception as e:
        print(f"❌ Erro ao salvar gráfico: {e}")
        return False

# ============================================
# FUNÇÃO: ENVIAR RELATÓRIO EM TEMPO REAL
# ============================================

async def enviar_relatorio_tempo_real():
    """Envia relatório em tempo real para o grupo"""
    dados = ler_dados_firebase()
    if dados:
        mensagem = gerar_relatorio_tempo_real(dados)
        await bot_application.bot.send_message(
            chat_id=CHAT_ID,
            text=mensagem,
            parse_mode="HTML"
        )
        print(f"✅ Relatório enviado às {datetime.now().strftime('%H:%M')}")
        return True
    else:
        print(f"❌ Falha ao enviar relatório às {datetime.now().strftime('%H:%M')}")
        return False

# ============================================
# FUNÇÃO: ENVIAR RELATÓRIO DIÁRIO (com gráfico)
# ============================================

async def enviar_relatorio_diario():
    """Gera gráfico, salva no Firebase e envia relatório no grupo"""
    print(f"📊 Gerando relatório diário às {datetime.now().strftime('%H:%M')}...")
    
    dados = buscar_historico_dia()
    if not dados:
        print("❌ Dados não encontrados")
        await bot_application.bot.send_message(
            chat_id=CHAT_ID,
            text="⚠️ Dados insuficientes para gerar o relatório diário."
        )
        return False
    
    grafico, relatorio = gerar_grafico_diario(dados)
    if not grafico:
        print(f"❌ {relatorio}")
        await bot_application.bot.send_message(
            chat_id=CHAT_ID,
            text=f"⚠️ {relatorio}"
        )
        return False
    
    # 1. Salva o gráfico no Firebase
    salvar_grafico_firebase(grafico, relatorio)
    
    # 2. Envia o relatório em texto no grupo
    await bot_application.bot.send_message(
        chat_id=CHAT_ID,
        text=relatorio + "\n\n📊 Clique em '📈 Gráfico Diário' abaixo para ver o gráfico:",
        parse_mode="HTML"
    )
    
    # 3. Envia a imagem do gráfico no grupo
    grafico.seek(0)
    await bot_application.bot.send_photo(
        chat_id=CHAT_ID,
        photo=grafico,
        caption="📊 Evolução completa do dia (PM2.5, PM10, Temperatura, Umidade)"
    )
    
    print(f"✅ Relatório diário enviado às {datetime.now().strftime('%H:%M')}")
    return True

# ============================================
# FUNÇÃO: VERIFICAR HORÁRIOS PROGRAMADOS
# ============================================

async def verificar_horarios():
    """Verifica se é hora de enviar os relatórios"""
    hora_atual = datetime.now().strftime("%H:%M")
    
    # Usa um dicionário para controlar o último envio
    if not hasattr(verificar_horarios, "ultimo_envio"):
        verificar_horarios.ultimo_envio = {}
    
    # Relatórios em tempo real (07h, 12h, 15h, 19h)
    for horario in HORARIOS_REPORT:
        if hora_atual == horario and verificar_horarios.ultimo_envio.get(horario) != datetime.now().date():
            print(f"⏰ Hora de enviar relatório: {horario}")
            await enviar_relatorio_tempo_real()
            verificar_horarios.ultimo_envio[horario] = datetime.now().date()
            await asyncio.sleep(30)  # Espera 30 segundos para não enviar múltiplas vezes
            return True
    
    # Relatório diário com gráfico (20h)
    if hora_atual == HORA_GRAFICO and verificar_horarios.ultimo_envio.get("diario") != datetime.now().date():
        print(f"⏰ Hora de enviar relatório diário com gráfico!")
        await enviar_relatorio_diario()
        verificar_horarios.ultimo_envio["diario"] = datetime.now().date()
        await asyncio.sleep(30)
        return True
    
    return False

# ============================================
# FUNÇÃO: ENVIAR GRÁFICO PARA O PRIVADO
# ============================================

def enviar_grafico_telegram_privado(chat_id):
    """Busca o gráfico do Firebase e envia para o privado do usuário"""
    print(f"📊 Buscando gráfico no Firebase para {chat_id}...")
    
    try:
        ref = db.reference('/grafico_diario')
        dados = ref.get()
        if not dados or 'imagem' not in dados:
            print("❌ Gráfico não encontrado no Firebase")
            return False
        
        imagem_bytes = base64.b64decode(dados['imagem'])
        print(f"✅ Gráfico encontrado! Tamanho: {len(imagem_bytes)} bytes")
        
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
        files = {'photo': ('grafico.png', imagem_bytes, 'image/png')}
        data = {'chat_id': chat_id, 'caption': '📊 Relatório Diário - Qualidade do Ar'}
        
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
# COMANDOS DO TELEGRAM (BOTÕES)
# ============================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    
    if chat.type == "private":
        await update.message.reply_text(
            "✅ Comando recebido!\n"
            "📌 Os botões estão disponíveis no grupo.\n"
            "   Clique em um botão lá para receber as informações aqui."
        )
        return
    
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
    
    try:
        await chat.pin_message(mensagem.message_id)
        print("📌 Mensagem fixada no grupo!")
    except Exception as e:
        print(f"⚠️ Não foi possível fixar: {e}")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("✅ Processando...")
    
    user_id = query.from_user.id
    user_name = query.from_user.first_name or "Usuário"
    
    dados = ler_dados_firebase()
    
    if query.data == "relatorio":
        mensagem = gerar_relatorio_tempo_real(dados)
    elif query.data == "previsao":
        mensagem = gerar_previsao(dados)  # Você já tem esta função
    elif query.data == "alertas":
        mensagem = gerar_alertas()  # Você já tem esta função
    elif query.data == "grafico":
        sucesso = enviar_grafico_telegram_privado(user_id)
        if sucesso:
            mensagem = "📊 Gráfico enviado no seu privado!"
        else:
            mensagem = "⚠️ Gráfico indisponível no momento. Aguarde o relatório das 20h."
    else:
        mensagem = "⚠️ Comando não reconhecido!"
    
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=mensagem + "\n\n━━━━━━━━━━━━━━━━━━━━━━\n📊 Use /start no grupo para ver os botões novamente."
        )
        await context.bot.send_message(
            chat_id=query.message.chat.id,
            text=f"✅ {user_name}, a resposta foi enviada no seu privado! 📩"
        )
        await query.answer("✅ Mensagem enviada no seu privado!")
    except Exception as e:
        print(f"❌ Erro ao enviar privado: {e}")
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
# FUNÇÕES: PREVISÃO E ALERTAS
# ============================================

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

# ============================================
# LOOP PRINCIPAL (para relatórios programados)
# ============================================

async def main_loop():
    """Loop que verifica os horários programados"""
    while True:
        try:
            await verificar_horarios()
            await asyncio.sleep(60)  # Verifica a cada 1 minuto
        except Exception as e:
            print(f"❌ Erro no loop: {e}")
            await asyncio.sleep(60)

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
    print("⏰ Relatórios programados:")
    print("   07h, 12h, 15h, 19h - Dados em tempo real")
    print("   20h - Relatório diário com gráfico")
    print("━━━━━━━━━━━━━━━━━━━━━━")
    
    bot_application = Application.builder().token(TELEGRAM_TOKEN).build()
    bot_application.add_handler(CommandHandler("start", start))
    bot_application.add_handler(CallbackQueryHandler(button_callback))
    
    app_initialized = False
    
    # Inicia o servidor
    port = int(os.environ.get("PORT", 8000))
    
    # Configura e inicia o loop de relatórios
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # Inicia o loop de relatórios em background
    loop.create_task(main_loop())
    
    # Inicia o servidor
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")

if __name__ == "__main__":
    main()
