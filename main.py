import os
import time
import json
import base64
import requests
import threading
import asyncio
from datetime import datetime, timedelta
import firebase_admin
from firebase_admin import credentials, db
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse

# ============================================
# VARIÁVEL PARA CONTROLAR A MENSAGEM DE BOAS-VINDAS
# ============================================

ultima_mensagem_boas_vindas = None

# ============================================
# VARIÁVEL PARA CONTROLAR O ÚLTIMO ALERTA ENVIADO
# ============================================

ultimo_alerta_enviado = None
ultimo_alerta_texto = ""
ultimo_alerta_timestamp = 0
INTERVALO_REENVIO_ALERTA = 21600  # 6 horas em segundos

# ============================================
# AJUSTE DE FUSO HORÁRIO (Brasília UTC-3)
# ============================================

os.environ['TZ'] = 'America/Sao_Paulo'
try:
    time.tzset()
except AttributeError:
    pass

# Funções de horário
def hora_brasilia():
    agora_utc = datetime.utcnow()
    return agora_utc - timedelta(hours=3)

def agora_str():
    return hora_brasilia().strftime("%H:%M")

def hoje_str():
    return hora_brasilia().strftime("%d/%m/%Y")

def data_iso():
    return hora_brasilia().strftime("%Y-%m-%d")

# ============================================
# CONFIGURAÇÕES
# ============================================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
FIREBASE_CRED_JSON = os.environ.get("FIREBASE_CRED_JSON")
FIREBASE_URL = "https://qualidade-do-ar-tcc-default-rtdb.firebaseio.com/"

# Coordenadas da sua cidade (substitua pelas suas)
LATITUDE = -22.0739   # ← Substitua pela sua latitude
LONGITUDE = -48.7403  # ← Substitua pela sua longitude

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
    print(f"🕐 Horário de Brasília: {agora_str()}")
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
# FUNÇÃO: PREVISÃO DO TEMPO VIA OPEN-METEO
# ============================================

def obter_previsao_tempo():
    """
    Busca previsão do tempo para sua localização via Open-Meteo API
    Agora com previsão para 3 dias
    """
    try:
        # Mudamos forecast_days para 4 (hoje + 3 dias)
        url = f"https://api.open-meteo.com/v1/forecast?latitude={LATITUDE}&longitude={LONGITUDE}&current=temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,sunrise,sunset&timezone=America/Sao_Paulo&forecast_days=4"
        
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            print(f"❌ Erro na Open-Meteo: {response.status_code}")
            return None
        
        dados = response.json()
        
        # Pega dados atuais
        current = dados.get('current', {})
        daily = dados.get('daily', {})
        
        # Código de clima (weather_code)
        weather_codes = {
            0: "☀️ Céu limpo",
            1: "🌤️ Principalmente limpo",
            2: "⛅ Parcialmente nublado",
            3: "☁️ Nublado",
            45: "🌫️ Neblina",
            48: "🌫️ Neblina com geada",
            51: "🌧️ Garoa leve",
            53: "🌧️ Garoa moderada",
            55: "🌧️ Garoa densa",
            61: "🌧️ Chuva leve",
            63: "🌧️ Chuva moderada",
            65: "🌧️ Chuva forte",
            71: "❄️ Neve leve",
            73: "❄️ Neve moderada",
            75: "❄️ Neve forte",
            80: "⛈️ Pancadas de chuva",
            81: "⛈️ Pancadas moderadas",
            82: "⛈️ Pancadas fortes",
            95: "⛈️ Trovoada",
            96: "⛈️ Trovoada com granizo",
            99: "⛈️ Trovoada com granizo forte"
        }
        
        weather_code = current.get('weather_code', 0)
        current_weather = weather_codes.get(weather_code, "❓ Desconhecido")
        
        # Dicionário para dias da semana
        dias_semana = {
            0: "Segunda-feira",
            1: "Terça-feira", 
            2: "Quarta-feira",
            3: "Quinta-feira",
            4: "Sexta-feira",
            5: "Sábado",
            6: "Domingo"
        }
        
        # Previsão para hoje e próximos dias
        hoje = data_iso()
        previsao_hoje = {}
        previsao_proximos_dias = []
        
        if 'time' in daily and len(daily['time']) >= 4:
            for i, data in enumerate(daily['time']):
                # Converte a data para dia da semana
                data_obj = datetime.strptime(data, '%Y-%m-%d')
                dia_semana = dias_semana[data_obj.weekday()]
                
                previsao_dia = {
                    'data': data,
                    'dia_semana': dia_semana,
                    'temp_max': daily['temperature_2m_max'][i],
                    'temp_min': daily['temperature_2m_min'][i],
                    'precipitacao': daily['precipitation_sum'][i],
                    'weather_code': daily['weather_code'][i],
                    'nascer_sol': daily['sunrise'][i].split('T')[1] if 'sunrise' in daily else '',
                    'por_sol': daily['sunset'][i].split('T')[1] if 'sunset' in daily else ''
                }
                
                if data == hoje:
                    previsao_hoje = previsao_dia
                else:
                    previsao_proximos_dias.append(previsao_dia)
        
        return {
            'atual': {
                'temp': current.get('temperature_2m', 0),
                'umid': current.get('relative_humidity_2m', 0),
                'sensacao': current.get('apparent_temperature', 0),
                'precipitacao': current.get('precipitation', 0),
                'clima': current_weather,
                'weather_code': weather_code,
                'wind_speed': current.get('wind_speed_10m', 0)
            },
            'hoje': previsao_hoje,
            'proximos_dias': previsao_proximos_dias[:3]  # Pega apenas os 3 próximos dias
        }
        
    except Exception as e:
        print(f"❌ Erro na Open-Meteo: {e}")
        return None

# ============================================
# FUNÇÃO: ANALISAR PREVISÃO DO TEMPO
# ============================================

def analisar_previsao(dados_sensor, previsao):
    """Analisa os dados do sensor com a previsão da Open-Meteo"""
    if not previsao:
        return "⚠️ Dados de previsão indisponíveis no momento."
    
    temp_sensor = dados_sensor.get('temperatura', 0) if dados_sensor else 0
    umid_sensor = dados_sensor.get('umidade', 0) if dados_sensor else 0
    
    previsao_atual = previsao.get('atual', {})
    previsao_hoje = previsao.get('hoje', {})
    proximos_dias = previsao.get('proximos_dias', [])
    
    temp_api = previsao_atual.get('temp', 0)
    clima_api = previsao_atual.get('clima', 'Desconhecido')
    temp_max_hoje = previsao_hoje.get('temp_max', 0)
    temp_min_hoje = previsao_hoje.get('temp_min', 0)
    precipitacao_hoje = previsao_hoje.get('precipitacao', 0)
    weather_code_hoje = previsao_hoje.get('weather_code', 0)
    wind_speed = previsao_atual.get('wind_speed', 0)
    
    # ============================================
    # ANÁLISE DA TEMPERATURA
    # ============================================
    
    diferenca_temp = abs(temp_sensor - temp_api)
    
    if temp_sensor > 0:
        if diferenca_temp > 2:
            analise_temp = f"⚠️ Seu sensor ({temp_sensor:.1f}°C) está {diferenca_temp:.1f}°C diferente da previsão ({temp_api:.1f}°C)."
        else:
            analise_temp = f"✅ Sensor ({temp_sensor:.1f}°C) alinhado com a previsão ({temp_api:.1f}°C)."
    
    # ============================================
    # EXPLICAÇÃO DA DIFERENÇA
    # ============================================
    
    explicacao_diferenca = ""
    if diferenca_temp > 2:
        if temp_sensor > temp_api:
            explicacao_diferenca = f"(Observação: A diferença entre a PREVISÃO e a Temperatura ATUAL do sensor é que a temperatura ainda poderá diminuir conforme o decorrer do dia, se aproximando da previsão de {temp_api:.1f}°C)"
        else:
            explicacao_diferenca = f"(Observação: A diferença entre a PREVISÃO e a Temperatura ATUAL do sensor é que a temperatura ainda poderá subir conforme o decorrer do dia, se aproximando da previsão de {temp_api:.1f}°C)"
    
    # ============================================
    # ANÁLISE DE TENDÊNCIA
    # ============================================
    
    tendencia = ""
    if temp_max_hoje > 0 and temp_sensor > 0:
        if temp_max_hoje > temp_sensor + 5:
            tendencia = "🌡️ Tendência de AQUECIMENTO nas próximas horas."
        elif temp_min_hoje < temp_sensor - 5:
            tendencia = "🌡️ Tendência de RESFRIAMENTO nas próximas horas."
        elif temp_max_hoje <= temp_sensor + 2:
            tendencia = "🌡️ Temperatura estável, sem grandes mudanças previstas."
    
    # ============================================
    # ANÁLISE DE CHUVA
    # ============================================
    
    chuva = ""
    if precipitacao_hoje > 5:
        chuva = "🌧️ Previsão de CHUVA para hoje. Recomenda-se precaução."
    elif precipitacao_hoje > 1:
        chuva = "🌦️ Possibilidade de CHUVA FRACA hoje."
    else:
        chuva = "☀️ Sem previsão de chuva para hoje."
    
    # ============================================
    # ANÁLISE DE UMIDADE
    # ============================================
    
    umid_analise = ""
    if umid_sensor > 0:
        if umid_sensor > 80:
            umid_analise = "💧 Umidade alta (>80%). Ambiente úmido."
        elif umid_sensor < 40:
            umid_analise = "💨 Umidade baixa (<40%). Ambiente seco."
        else:
            umid_analise = "💧 Umidade confortável."
    
    # ============================================
    # PREVISÃO PARA OS PRÓXIMOS 3 DIAS
    # ============================================
    
    previsao_dias = ""
    if proximos_dias:
        previsao_dias = "\n━━━━━━━━━━━━━━━━━━━━━━\n📅 Previsão para os próximos 3 dias\n"
        
        for i, dia in enumerate(proximos_dias[:3]):
            # Emoji para o clima
            weather_code = dia.get('weather_code', 0)
            if weather_code in [0, 1]:
                clima_emoji = "☀️"
            elif weather_code in [2, 3]:
                clima_emoji = "⛅"
            elif weather_code in [45, 48]:
                clima_emoji = "🌫️"
            elif weather_code in [51, 53, 55, 61, 63, 65, 80, 81, 82]:
                clima_emoji = "🌧️"
            elif weather_code in [71, 73, 75]:
                clima_emoji = "❄️"
            elif weather_code in [95, 96, 99]:
                clima_emoji = "⛈️"
            else:
                clima_emoji = "☁️"
            
            previsao_dias += f"\n{dia.get('dia_semana', 'Dia')}:\n"
            previsao_dias += f"   🌡️ Máxima: {dia.get('temp_max', 0):.1f}°C\n"
            previsao_dias += f"   🌡️ Mínima: {dia.get('temp_min', 0):.1f}°C\n"
            previsao_dias += f"   🌧️ Chuva: {dia.get('precipitacao', 0):.1f} mm\n"
            previsao_dias += f"   {clima_emoji} {dia.get('weather_code', 'N/A')}\n"
        
        # ============================================
        # RESUMO DA TENDÊNCIA PARA OS PRÓXIMOS DIAS
        # ============================================
        
        # Analisa a tendência de temperatura
        if len(proximos_dias) >= 2:
            temp_atual = temp_sensor
            temp_futura = proximos_dias[0].get('temp_max', temp_atual)
            temp_ultimo = proximos_dias[2].get('temp_max', temp_futura) if len(proximos_dias) >= 3 else temp_futura
            
            if temp_ultimo > temp_atual + 3:
                tendencia_futura = "📈 Previsão de AUMENTO de temperatura nos próximos dias."
            elif temp_ultimo < temp_atual - 3:
                tendencia_futura = "📉 Previsão de DIMINUIÇÃO de temperatura nos próximos dias."
            else:
                tendencia_futura = "➡️ Previsão de temperatura ESTÁVEL nos próximos dias."
            
            # Verifica se vai chover nos próximos dias
            chuva_futura = False
            for dia in proximos_dias[:3]:
                if dia.get('precipitacao', 0) > 1:
                    chuva_futura = True
                    break
            
            if chuva_futura:
                tendencia_futura += " 🌧️ Possibilidade de chuva nos próximos dias."
            else:
                tendencia_futura += " ☀️ Tempo seco e estável nos próximos dias."
            
            previsao_dias += f"\n{tendencia_futura}"
    
    # ============================================
    # MONTAGEM DA MENSAGEM COMPLETA
    # ============================================
    
    mensagem = f"""🌤️ PREVISÃO DO TEMPO COMPLETA
━━━━━━━━━━━━━━━━━━━━━━
📊 Dados do sensor:
   🌡️ Temperatura: {temp_sensor:.1f}°C
   💧 Umidade: {umid_sensor:.0f}%
━━━━━━━━━━━━━━━━━━━━━━
📡 Previsão Open-Meteo:
   🌡️ Temperatura atual: {temp_api:.1f}°C
   🌡️ Sensação térmica: {previsao_atual.get('sensacao', 0):.1f}°C
   ☁️ Clima: {clima_api}
   💧 Umidade: {previsao_atual.get('umid', 0):.0f}%
   🌧️ Precipitação: {previsao_atual.get('precipitacao', 0):.1f} mm
   💨 Vento: {wind_speed:.1f} km/h
━━━━━━━━━━━━━━━━━━━━━━
📅 Previsão para hoje:
   🌡️ Máxima: {temp_max_hoje:.1f}°C
   🌡️ Mínima: {temp_min_hoje:.1f}°C
   🌧️ Chuva: {precipitacao_hoje:.1f} mm
{previsao_dias}
━━━━━━━━━━━━━━━━━━━━━━
🔮 Análise:
{analise_temp}
{tendencia}
{chuva}
{umid_analise}

{explicacao_diferenca}
━━━━━━━━━━━━━━━━━━━━━━
📌 Atualizado: {agora_str()} - {hoje_str()}"""

    return mensagem

# ============================================
# FUNÇÃO: GERAR ALERTAS METEOROLÓGICOS (COM ANÁLISE COMBINADA)
# ============================================

def gerar_alertas_meteorologicos():
    """Gera alertas combinando dados do sensor com previsão Open-Meteo"""
    try:
        # 1. Busca dados do sensor
        dados_sensor = ler_dados_firebase()
        if not dados_sensor:
            return "⚠️ Dados do sensor indisponíveis."
        
        # 2. Busca previsão Open-Meteo
        previsao = obter_previsao_tempo()
        if not previsao:
            return "⚠️ Previsão do tempo indisponível no momento."
        
        # 3. Busca histórico para tendências
        ref = db.reference('/historico')
        historico = ref.get()
        
        temp_sensor = dados_sensor.get('temperatura', 0)
        umid_sensor = dados_sensor.get('umidade', 0)
        pressao_sensor = dados_sensor.get('pressao', 0)
        
        previsao_atual = previsao.get('atual', {})
        previsao_hoje = previsao.get('hoje', {})
        
        temp_api = previsao_atual.get('temp', 0)
        clima_api = previsao_atual.get('clima', 'Desconhecido')
        temp_max_hoje = previsao_hoje.get('temp_max', 0)
        temp_min_hoje = previsao_hoje.get('temp_min', 0)
        precipitacao_hoje = previsao_hoje.get('precipitacao', 0)
        weather_code_hoje = previsao_hoje.get('weather_code', 0)
        wind_speed = previsao_atual.get('wind_speed', 0)
        
        # Códigos de clima que indicam chuva
        codigos_chuva = [51, 53, 55, 61, 63, 65, 80, 81, 82, 95, 96, 99]
        
        alertas = "⚠️ ALERTAS METEOROLÓGICOS\n━━━━━━━━━━━━━━━━━━━━━━\n"
        tem_alerta = False
        
        # ============================================
        # ALERTA 1: TEMPERATURA (Variação em 1h + Previsão)
        # ============================================
        
        if historico:
            timestamps = sorted(historico.keys())
            if len(timestamps) >= 6:
                idx_antigo = len(timestamps) - 6
                dados_antigo = historico[timestamps[idx_antigo]]
                temp_antiga = dados_antigo.get('temperatura', temp_sensor)
                var_temp = temp_sensor - temp_antiga
                
                if abs(var_temp) >= 2.0:
                    tem_alerta = True
                    alertas += f"🌡️ ALERTA DE TEMPERATURA!\n"
                    
                    # 🔥 ONDA DE CALOR (Subida + Previsão de calor)
                    if var_temp > 0:
                        alertas += f"   🔥 Subida de {abs(var_temp):.1f}°C em 1h\n"
                        
                        # Combinação: sensor subiu + previsão confirma calor
                        if temp_max_hoje > 30 and temp_sensor > 25:
                            alertas += f"   ☀️ PREVISÃO CONFIRMA: Temp máxima de {temp_max_hoje:.1f}°C hoje\n"
                            alertas += f"   ⚠️ ONDA DE CALOR CONFIRMADA! Mantenha-se hidratado.\n"
                        elif temp_max_hoje > temp_sensor + 5:
                            alertas += f"   ⚠️ PREVISÃO INDICA: {temp_sensor:.1f}°C → {temp_max_hoje:.1f}°C\n"
                            alertas += f"   ⚠️ ONDA DE CALOR nas próximas horas!\n"
                        elif temp_max_hoje > 28:
                            alertas += f"   ⚠️ Dia quente previsto ({temp_max_hoje:.1f}°C). Fique atento!\n"
                        else:
                            alertas += f"   ⚠️ Aquecimento significativo, mas previsão indica clima ameno.\n"
                    
                    # ❄️ FRENTE FRIA (Queda + Previsão de frio)
                    else:
                        alertas += f"   ❄️ Queda de {abs(var_temp):.1f}°C em 1h\n"
                        
                        # Combinação: sensor caiu + previsão confirma frio
                        if temp_min_hoje < 18 and temp_sensor < 20:
                            alertas += f"   ❄️ PREVISÃO CONFIRMA: Temp mínima de {temp_min_hoje:.1f}°C hoje\n"
                            alertas += f"   ⚠️ FRENTE FRIA CONFIRMADA! Agasalhe-se.\n"
                        elif temp_min_hoje < temp_sensor - 3:
                            alertas += f"   ⚠️ PREVISÃO INDICA: {temp_sensor:.1f}°C → {temp_min_hoje:.1f}°C\n"
                            alertas += f"   ⚠️ FRENTE FRIA nas próximas horas!\n"
                        elif temp_min_hoje < 20:
                            alertas += f"   ⚠️ Noite fria prevista ({temp_min_hoje:.1f}°C). Prepare-se!\n"
                        else:
                            alertas += f"   ⚠️ Resfriamento significativo, mas previsão indica clima ameno.\n"
                    
                    alertas += "━━━━━━━━━━━━━━━━━━━━━━\n"
        
        # ============================================
        # ALERTA 2: PRESSÃO (Variação em 1h)
        # ============================================
        
        if historico:
            timestamps = sorted(historico.keys())
            if len(timestamps) >= 6:
                idx_antigo = len(timestamps) - 6
                dados_antigo = historico[timestamps[idx_antigo]]
                press_antiga = dados_antigo.get('pressao', pressao_sensor)
                var_press = pressao_sensor - press_antiga
                
                if abs(var_press) >= 5.0:
                    tem_alerta = True
                    alertas += f"📊 ALERTA DE PRESSÃO!\n"
                    if var_press < 0:
                        alertas += f"   ⬇️ Queda de {abs(var_press):.1f} hPa em 1h\n"
                        if precipitacao_hoje > 5 or weather_code_hoje in codigos_chuva:
                            alertas += f"   🌧️ Previsão de {precipitacao_hoje:.1f}mm de chuva\n"
                            alertas += f"   ⚠️ Possibilidade de TEMPESTADE!\n"
                        else:
                            alertas += f"   ⚠️ Possível mudança climática!\n"
                    else:
                        alertas += f"   ⬆️ Subida de {var_press:.1f} hPa em 1h\n"
                        alertas += f"   ☀️ Tendência de tempo estável e melhora!\n"
                    alertas += "━━━━━━━━━━━━━━━━━━━━━━\n"
        
        # ============================================
        # ALERTA 3: CHUVA (baseado no código do clima E precipitação)
        # ============================================
        
        if weather_code_hoje in codigos_chuva or precipitacao_hoje > 5:
            tem_alerta = True
            alertas += f"🌧️ ALERTA DE CHUVA!\n"
            alertas += f"   ☔ Previsão de {precipitacao_hoje:.1f}mm de chuva hoje\n"
            
            # Identifica o tipo de chuva pelo código
            if weather_code_hoje in [95, 96, 99]:
                alertas += f"   ⛈️ TROVOADA! Cuidado com raios e ventos fortes.\n"
            elif weather_code_hoje in [80, 81, 82]:
                alertas += f"   ⚡ PANCADAS DE CHUVA! Pode alagar rapidamente.\n"
            elif weather_code_hoje in [61, 63, 65]:
                alertas += f"   🌧️ Chuva constante. Leve guarda-chuva.\n"
            elif weather_code_hoje in [51, 53, 55]:
                alertas += f"   🌦️ Garoa fina. Pode molhar.\n"
            
            if precipitacao_hoje > 20:
                alertas += f"   ⚠️ CHUVA FORTE! Cuidado com enchentes.\n"
            elif precipitacao_hoje > 10:
                alertas += f"   ⚠️ Chuva moderada.\n"
            alertas += "━━━━━━━━━━━━━━━━━━━━━━\n"
        
        # ============================================
        # ALERTA 4: CLIMA EXTREMO (Com base na previsão)
        # ============================================
        
        weather_code = previsao_atual.get('weather_code', 0)
        if weather_code in [95, 96, 99]:  # Trovoada
            tem_alerta = True
            alertas += f"⛈️ ALERTA DE TROVOADA!\n"
            alertas += f"   ⚠️ Possibilidade de raios e ventos fortes!\n"
            alertas += f"   🏠 Permaneça em local seguro.\n"
            alertas += "━━━━━━━━━━━━━━━━━━━━━━\n"
        
        # ============================================
        # ALERTA 5: VENTOS FORTES (Baseado na Open-Meteo)
        # ============================================
        
        if wind_speed > 40:  # > 40 km/h
            tem_alerta = True
            alertas += f"🌬️ ALERTA DE VENTOS FORTES!\n"
            alertas += f"   💨 Velocidade: {wind_speed:.0f} km/h\n"
            if wind_speed > 60:
                alertas += f"   ⚠️ VENTOS MUITO FORTES! Risco de queda de árvores.\n"
            elif wind_speed > 50:
                alertas += f"   ⚠️ Ventos fortes. Cuidado com objetos soltos.\n"
            else:
                alertas += f"   ⚠️ Ventos moderados a fortes. Preste atenção.\n"
            alertas += "━━━━━━━━━━━━━━━━━━━━━━\n"
        
        # ============================================
        # SE NÃO HOUVER ALERTA
        # ============================================
        
        if not tem_alerta:
            alertas += "✅ TEMPO ESTÁVEL!\n"
            alertas += "━━━━━━━━━━━━━━━━━━━━━━\n"
            alertas += f"🌡️ {temp_sensor:.1f}°C | 💧 {umid_sensor:.0f}%\n"
            alertas += f"📊 {pressao_sensor:.0f} hPa\n"
            alertas += f"☁️ {clima_api}\n"
            if wind_speed > 20:
                alertas += f"💨 Vento: {wind_speed:.0f} km/h\n"
            alertas += "━━━━━━━━━━━━━━━━━━━━━━\n"
            alertas += "Nenhum alerta meteorológico previsto.\n"
            alertas += "Condições climáticas estáveis.\n"
            alertas += f"🌡️ Previsão para hoje: {temp_max_hoje:.1f}°C (máx)"
        
        return alertas
        
    except Exception as e:
        print(f"❌ Erro ao gerar alertas: {e}")
        return f"❌ Erro ao gerar alertas: {e}"

# ============================================
# 🔥 FUNÇÃO: VERIFICAR E ENVIAR ALERTAS AUTOMÁTICOS 
# ============================================

async def verificar_e_enviar_alertas():
    """Verifica se há alertas meteorológicos e envia automaticamente no grupo"""
    global ultimo_alerta_enviado, ultimo_alerta_texto, ultimo_alerta_timestamp
    
    try:
        # Gera os alertas
        alerta = gerar_alertas_meteorologicos()
        
        # Se não houver alerta (tempo estável), não faz nada
        if "✅ TEMPO ESTÁVEL!" in alerta:
            # Reseta o timestamp do último alerta quando o tempo estabiliza
            # Isso evita que um alerta antigo seja reenviado depois
            if ultimo_alerta_enviado is not None:
                print("✅ Tempo estabilizado. Resetando controle de alertas.")
                ultimo_alerta_enviado = None
                ultimo_alerta_texto = ""
            return
        
        # 🔧 REGRA 1: Verifica se o alerta é IGUAL ao último enviado
        if alerta == ultimo_alerta_texto:
            # É igual! Verifica se já passou 6 horas
            if ultimo_alerta_timestamp > 0:
                tempo_decorrido = time.time() - ultimo_alerta_timestamp
                horas_decorridas = tempo_decorrido / 3600
                
                print(f"⏳ Alerta IGUAL ao último. Último enviado há {horas_decorridas:.1f} horas.")
                
                # 🔧 REGRA 2: Se passou 6 horas, reenvia (mesmo sendo igual)
                if tempo_decorrido >= INTERVALO_REENVIO_ALERTA:
                    print(f"🔄 Passaram 6 horas desde o último alerta igual. Reenviando...")
                    # Envia o alerta
                    await bot_application.bot.send_message(
                        chat_id=CHAT_ID,
                        text=alerta + "\n\n━━━━━━━━━━━━━━━━━━━━━━\n📌 Este alerta já havia sido enviado anteriormente.",
                        parse_mode="HTML"
                    )
                    # Atualiza o timestamp (reset da contagem)
                    ultimo_alerta_timestamp = time.time()
                    print(f"🚨 Alerta reenviado (após 6h) às {agora_str()}")
                    return
                else:
                    # Ainda não passou 6 horas, NÃO ENVIA
                    faltam = INTERVALO_REENVIO_ALERTA - tempo_decorrido
                    print(f"⏳ Aguardando {faltam/3600:.1f} horas para reenviar.")
                    return
        
        # 🔧 ALERTA NOVO! Envia imediatamente
        await bot_application.bot.send_message(
            chat_id=CHAT_ID,
            text=alerta,
            parse_mode="HTML"
        )
        
        # Atualiza as variáveis de controle
        ultimo_alerta_enviado = time.time()
        ultimo_alerta_texto = alerta
        ultimo_alerta_timestamp = time.time()
        print(f"🚨 NOVO alerta meteorológico enviado às {agora_str()}")
        
    except Exception as e:
        print(f"❌ Erro ao verificar/enviar alertas: {e}")
        
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
    hora = agora_str()
    data = hoje_str()
    
    # Busca tendência
    try:
        ref = db.reference('/historico')
        historico = ref.get()
        if historico:
            timestamps = sorted(historico.keys())
            if len(timestamps) >= 2:
                ultimo = historico[timestamps[-1]]
                pm25_anterior = ultimo.get('pm25', pm25)
                if pm25 > pm25_anterior + 0.1:
                    tendencia = "📈 Subindo"
                elif pm25 < pm25_anterior - 0.1:
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
# FUNÇÃO: GERAR GRÁFICO (USANDO MATPLOTLIB) - CORRIGIDA
# ============================================

def gerar_grafico_diario(dados):
    """Gera gráfico com 4 painéis usando matplotlib - VERSÃO FINAL"""
    
    # 🔧 Importa matplotlib de forma segura
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import io
        from datetime import datetime as dt
    except ImportError as e:
        print(f"❌ Matplotlib não disponível: {e}")
        return None, "Biblioteca matplotlib não disponível. Instale com: pip install matplotlib"

    # 🔧 Funções auxiliares (fallback se não existirem no escopo global)
    def _classificar(pm25):
        if pm25 <= 15: return "BOA"
        elif pm25 <= 25: return "MODERADA"
        elif pm25 <= 50: return "RUIM"
        elif pm25 <= 100: return "MUITO RUIM"
        else: return "PÉSSIMA"
    
    def _emoji(pm25):
        if pm25 <= 15: return "🟢"
        elif pm25 <= 25: return "🟡"
        elif pm25 <= 50: return "🟠"
        elif pm25 <= 100: return "🔴"
        else: return "⚫"
    
    # Tenta usar funções globais se existirem
    classificar_ar_func = globals().get('classificar_ar', _classificar)
    get_emoji_func = globals().get('get_emoji_classificacao', _emoji)
    hoje_str_func = globals().get('hoje_str', lambda: dt.now().strftime("%d/%m/%Y"))
    agora_str_func = globals().get('agora_str', lambda: dt.now().strftime("%H:%M"))

    try:
        # 🔧 VALIDAÇÃO
        if not dados or len(dados) < 2:
            return None, f"Dados insuficientes: {len(dados) if dados else 0} registros (mínimo 2)."

        # 🔧 LIMITA DADOS
        dados = dados[-100:] if len(dados) > 100 else dados

        # 🔧 PROCESSAMENTO
        indices, horas, pm25, pm10, temp, umid = [], [], [], [], [], []
        
        for idx, d in enumerate(dados):
            if not isinstance(d, dict):
                continue
            
            ts = d.get('timestamp', 0)
            hora = dt.fromtimestamp(ts).strftime('%H:%M') if isinstance(ts, (int, float)) and ts > 0 else "--:--"
            
            pm25.append(float(d.get('pm25') or 0))
            pm10.append(float(d.get('pm10') or 0))
            temp.append(float(d.get('temp') or 0))
            umid.append(float(d.get('umid') or 0))
            indices.append(idx)
            horas.append(hora)

        if len(pm25) < 2:
            return None, f"Dados insuficientes após processamento: {len(pm25)} registros."

        # 🔧 ESTATÍSTICAS
        media_pm25 = sum(pm25) / len(pm25)
        max_pm25 = max(pm25)
        min_pm25 = min(pm25)
        max_idx = pm25.index(max_pm25)
        max_pm25_hora = horas[max_idx] if max_idx < len(horas) else "--:--"
        
        classificacao = classificar_ar_func(media_pm25)
        emoji = get_emoji_func(media_pm25)

        # 🔧 GRÁFICO
        fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(14, 10))

        # PM2.5
        ax1.bar(indices, pm25, color='#1f77b4', alpha=0.7, width=0.6, label='PM2.5')
        ax1.plot(indices, pm25, 'o-', color='darkblue', linewidth=1.5, markersize=3, label='Tendência')
        ax1.axhline(y=media_pm25, color='red', linestyle='--', alpha=0.5, label=f'Média: {media_pm25:.1f}')
        ax1.set_title(f'PM2.5 - Média: {media_pm25:.1f} | Máx: {max_pm25:.1f} @ {max_pm25_hora}')
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        if len(horas) > 0:
            step = max(1, len(horas)//10)
            ax1.set_xticks(indices[::step])
            ax1.set_xticklabels(horas[::step], rotation=45, ha='right', fontsize=7)

        # PM10
        media_pm10 = sum(pm10) / len(pm10) if pm10 else 0
        ax2.bar(indices, pm10, color='#ff7f0e', alpha=0.7, width=0.6, label='PM10')
        ax2.plot(indices, pm10, 's-', color='darkred', linewidth=1.5, markersize=3, label='Tendência')
        ax2.axhline(y=media_pm10, color='red', linestyle='--', alpha=0.5, label=f'Média: {media_pm10:.1f}')
        ax2.set_title(f'PM10 - Média: {media_pm10:.1f}')
        ax2.legend()
        ax2.grid(True, alpha=0.3)
        if len(horas) > 0:
            step = max(1, len(horas)//10)
            ax2.set_xticks(indices[::step])
            ax2.set_xticklabels(horas[::step], rotation=45, ha='right', fontsize=7)

        # Temperatura
        media_temp = sum(temp) / len(temp) if temp else 0
        ax3.plot(indices, temp, 'o-', color='#2ca02c', linewidth=2, markersize=4, label='Temperatura')
        ax3.fill_between(indices, temp, alpha=0.2, color='#2ca02c')
        ax3.axhline(y=media_temp, color='red', linestyle='--', alpha=0.5, label=f'Média: {media_temp:.1f}°C')
        ax3.set_title(f'Temperatura - Média: {media_temp:.1f}°C')
        ax3.legend()
        ax3.grid(True, alpha=0.3)
        if len(horas) > 0:
            step = max(1, len(horas)//10)
            ax3.set_xticks(indices[::step])
            ax3.set_xticklabels(horas[::step], rotation=45, ha='right', fontsize=7)

        # Umidade
        media_umid = sum(umid) / len(umid) if umid else 0
        ax4.plot(indices, umid, 's-', color='#9467bd', linewidth=2, markersize=4, label='Umidade')
        ax4.fill_between(indices, umid, alpha=0.2, color='#9467bd')
        ax4.axhline(y=media_umid, color='red', linestyle='--', alpha=0.5, label=f'Média: {media_umid:.0f}%')
        ax4.set_title(f'Umidade - Média: {media_umid:.0f}%')
        ax4.legend()
        ax4.grid(True, alpha=0.3)
        if len(horas) > 0:
            step = max(1, len(horas)//10)
            ax4.set_xticks(indices[::step])
            ax4.set_xticklabels(horas[::step], rotation=45, ha='right', fontsize=7)

        data_str = hoje_str_func()
        fig.suptitle(f'📊 Qualidade do Ar - {data_str} | Classificação: {emoji} {classificacao}', fontsize=14, fontweight='bold')
        plt.tight_layout()

        # 🔧 SALVA
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=100, bbox_inches='tight')
        buf.seek(0)
        plt.close(fig)

        # 🔧 RELATÓRIO
        dados_finais = dados[-1] if dados else {}
        relatorio = f"""📈 RELATÓRIO DIÁRIO COMPLETO
🕐 {agora_str_func()} - {data_str}
━━━━━━━━━━━━━━━━━━━━━━
📊 Estatísticas do dia:
   Média PM2.5: {media_pm25:.1f} µg/m³
   Máximo PM2.5: {max_pm25:.1f} µg/m³
   Mínimo PM2.5: {min_pm25:.1f} µg/m³
   Classificação: {emoji} {classificacao}

🌡️ Condições finais:
   Temperatura: {dados_finais.get('temp', 0):.1f}°C
   Umidade: {dados_finais.get('umid', 0):.0f}%
   Pressão: {dados_finais.get('pressao', 0):.0f} hPa
   VOC: {dados_finais.get('voc', 0)}"""

        print(f"✅ Gráfico gerado com sucesso! {len(pm25)} pontos")
        return buf, relatorio

    except Exception as e:
        import traceback
        print(f"❌ Erro ao gerar gráfico: {e}")
        traceback.print_exc()
        return None, f"Erro ao gerar gráfico: {e}"

# ============================================
# FUNÇÃO: SALVAR GRÁFICO NO FIREBASE
# ============================================

def salvar_grafico_firebase(imagem_buffer, relatorio):
    """Salva o gráfico no Firebase para o botão 'Gráfico Diário'"""
    try:
        imagem_buffer.seek(0)
        imagem_base64 = base64.b64encode(imagem_buffer.getvalue()).decode('utf-8')
        
        ref = db.reference('/grafico_diario')
        ref.set({
            'imagem': imagem_base64,
            'data': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
            'timestamp': int(datetime.utcnow().timestamp())
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
        print(f"✅ Relatório enviado às {agora_str()} (Brasília)")
        return True
    else:
        print(f"❌ Falha ao enviar relatório às {agora_str()}")
        return False

# ============================================
# FUNÇÃO: ENVIAR RELATÓRIO DIÁRIO (com gráfico)
# ============================================

async def enviar_relatorio_diario():
    """Gera gráfico, salva no Firebase e envia relatório no grupo"""
    print(f"📊 Gerando relatório diário às {agora_str()}...")
    
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
    
    print(f"✅ Relatório diário enviado às {agora_str()}")
    return True

# ============================================
# FUNÇÃO: VERIFICAR HORÁRIOS PROGRAMADOS
# ============================================

async def verificar_horarios():
    """Verifica se é hora de enviar os relatórios (horário de Brasília)"""
    hora_atual = agora_str()
    
    # Usa um dicionário para controlar o último envio
    if not hasattr(verificar_horarios, "ultimo_envio"):
        verificar_horarios.ultimo_envio = {}
    
    data_hoje = hoje_str()
    
    # Relatórios em tempo real (07h, 12h, 15h, 19h)
    for horario in HORARIOS_REPORT:
        if hora_atual == horario and verificar_horarios.ultimo_envio.get(horario) != data_hoje:
            print(f"⏰ Hora de enviar relatório: {horario} (Brasília)")
            await enviar_relatorio_tempo_real()
            verificar_horarios.ultimo_envio[horario] = data_hoje
            await asyncio.sleep(30)
            return True
    
    # Relatório diário com gráfico (20h)
    if hora_atual == HORA_GRAFICO and verificar_horarios.ultimo_envio.get("diario") != data_hoje:
        print(f"⏰ Hora de enviar relatório diário com gráfico! (Brasília)")
        await enviar_relatorio_diario()
        verificar_horarios.ultimo_envio["diario"] = data_hoje
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
# FUNÇÃO: ENVIAR BOAS-VINDAS E APAGAR A ANTERIOR
# ============================================

async def enviar_boas_vindas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ultima_mensagem_boas_vindas
    
    # Verifica se é um novo membro entrando no grupo
    if update.message and update.message.new_chat_members:
        chat_id = update.effective_chat.id
        
        # 🔧 APAGA A MENSAGEM ANTERIOR (se existir)
        if ultima_mensagem_boas_vindas:
            try:
                await context.bot.delete_message(
                    chat_id=chat_id,
                    message_id=ultima_mensagem_boas_vindas
                )
                print("🗑️ Mensagem de boas-vindas anterior apagada!")
            except Exception as e:
                print(f"⚠️ Erro ao apagar mensagem anterior: {e}")
        
        # Envia a nova mensagem de boas-vindas
        keyboard = [
            [InlineKeyboardButton("📊 Relatório do Ar", callback_data="relatorio")],
            [InlineKeyboardButton("🌤️ Previsão do Tempo", callback_data="previsao")],
            [InlineKeyboardButton("⚠️ Alertas Meteorológicos", callback_data="alertas")],
            [InlineKeyboardButton("📈 Gráfico Diário", callback_data="grafico")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Nome do novo membro
        for member in update.message.new_chat_members:
            nome = member.first_name or "Novo membro"
            
            mensagem = await context.bot.send_message(
                chat_id=chat_id,
                text=f"👋 Seja bem-vindo(a), {nome}!\n\n"
                     f"🔹 SISTEMA DE QUALIDADE DO AR\n"
                     f"━━━━━━━━━━━━━━━━━━━━━━\n"
                     f"📊 Clique nos botões abaixo para receber as informações no seu privado:\n\n"
                     f"📌 As mensagens de boas-vindas são apagadas automaticamente para manter o grupo organizado.",
                reply_markup=reply_markup
            )
            
            # 🔧 GUARDA O ID DA NOVA MENSAGEM
            ultima_mensagem_boas_vindas = mensagem.message_id
            print(f"📌 Nova mensagem de boas-vindas guardada (ID: {ultima_mensagem_boas_vindas})")
            
            # Fixa a mensagem no topo do grupo
            try:
                await context.bot.pin_chat_message(
                    chat_id=chat_id,
                    message_id=mensagem.message_id
                )
                print("📌 Mensagem fixada no topo do grupo!")
            except Exception as e:
                print(f"⚠️ Não foi possível fixar: {e}")

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
        previsao = obter_previsao_tempo()
        if previsao:
            mensagem = analisar_previsao(dados, previsao)
        else:
            mensagem = "⚠️ Dados de previsão indisponíveis no momento. Tente novamente mais tarde."
    elif query.data == "alertas":
        mensagem = gerar_alertas_meteorologicos()
    elif query.data == "grafico":
        sucesso = enviar_grafico_telegram_privado(user_id)
        if sucesso:
            mensagem = "📊 Gráfico enviado no seu privado!"
        else:
            mensagem = "⚠️ Gráfico indisponível no momento. Aguarde o relatório das 20h."
    else:
        mensagem = "⚠️ Comando não reconhecido!"
    
    try:
        # Envia a resposta no privado
        await context.bot.send_message(
            chat_id=user_id,
            text=mensagem
        )
        
        # 🔧 Envia a confirmação no grupo e agenda para apagar
        msg_confirmacao = await context.bot.send_message(
            chat_id=query.message.chat.id,
            text=f"✅ {user_name}, a resposta foi enviada no seu privado! 📩"
        )
        
        # 🔧 Agenda a exclusão da mensagem após 15 segundos
        await asyncio.sleep(15)
        await context.bot.delete_message(
            chat_id=query.message.chat.id,
            message_id=msg_confirmacao.message_id
        )
        
        await query.answer("✅ Mensagem enviada no seu privado!")
        
    except Exception as e:
        print(f"❌ Erro ao enviar privado: {e}")
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

# ============================================
# LOOP PRINCIPAL (para relatórios programados + alertas)
# ============================================

async def main_loop():
    """Loop que verifica os horários programados (horário de Brasília) e alertas"""
    global bot_application
    
    print(f"🕐 Loop iniciado. Horário de Brasília: {agora_str()}")
    
    ultima_verificacao_alertas = 0
    
    while True:
        try:
            # Aguarda o bot_application ser inicializado
            if bot_application is None:
                await asyncio.sleep(5)
                continue
            
            # Verifica horários programados usando o bot_application global
            await verificar_horarios()
            
            # 🔥 Verifica alertas meteorológicos a cada 5 minutos
            if time.time() - ultima_verificacao_alertas >= 300:  # 5 minutos
                ultima_verificacao_alertas = time.time()
                await verificar_e_enviar_alertas()
            
            await asyncio.sleep(30)  # Verifica a cada 30 segundos
            
        except Exception as e:
            print(f"❌ Erro no loop: {e}")
            await asyncio.sleep(60)

# ============================================
# MAIN CORRIGIDO
# ============================================

def main():
    global bot_application, app_initialized
    
    print("🚀 Bot do Telegram (Render) iniciado!")
    print(f"🕐 Horário de Brasília: {agora_str()}")
    print("📊 4 botões disponíveis:")
    print("   📊 Relatório do Ar")
    print("   🌤️ Previsão do Tempo (com Open-Meteo)")
    print("   ⚠️ Alertas Meteorológicos")
    print("   📈 Gráfico Diário")
    print("━━━━━━━━━━━━━━━━━━━━━━")
    print("⏰ Relatórios programados (horário de Brasília):")
    print("   07h, 12h, 15h, 19h - Dados em tempo real")
    print("   20h - Relatório diário com gráfico")
    print("━━━━━━━━━━━━━━━━━━━━━━")
    print("🔥 Alertas meteorológicos automáticos: ATIVADOS")
    print("   ⏱️ Verificação a cada 5 minutos")
    print("   🚨 Envio automático no grupo quando detectado")
    print("   🌧️ Detecção de chuva por código do clima + precipitação")
    print("   🌬️ Detecção de ventos fortes")
    print(f"📍 Localização: {LATITUDE}, {LONGITUDE}")
    
    # Inicializa a aplicação do bot
    bot_application = Application.builder().token(TELEGRAM_TOKEN).build()
    bot_application.add_handler(CommandHandler("start", start))
    bot_application.add_handler(CallbackQueryHandler(button_callback))
    
    # Adiciona o handler para novos membros
    bot_application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, enviar_boas_vindas))
    
    app_initialized = False
    
    # ============================================
    # 🔧 CORREÇÃO: Inicia o loop usando asyncio.create_task
    # ============================================
    
    async def start_background_tasks():
        """Inicia as tarefas em background"""
        # Inicia o loop de relatórios
        asyncio.create_task(main_loop())
        print("✅ Loop de relatórios iniciado!")
    
    # Cria um loop de eventos para executar as tarefas em background
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # Inicia as tarefas em background
    loop.run_until_complete(start_background_tasks())
    
    # ============================================
    # Inicia o servidor
    # ============================================
    
    # Nota: O servidor precisa ser iniciado em uma thread separada
    # porque o loop de eventos já está rodando
    
    def run_server():
        port = int(os.environ.get("PORT", 8000))
        config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
        server = uvicorn.Server(config)
        server.run()
    
    # Inicia o servidor em uma thread separada
    import threading
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    print("✅ Servidor iniciado!")
    
    # Mantém o loop principal rodando
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        print("🛑 Servidor interrompido!")
    
    # ============================================
    # 🔧 CORREÇÃO: Executar o loop em uma thread separada
    # ============================================
    
    def run_background_loop():
        """Executa o loop de relatórios em background"""
        try:
            # Cria um novo loop de eventos para esta thread
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            
            # Executa o loop principal
            new_loop.run_until_complete(main_loop())
        except Exception as e:
            print(f"❌ Erro no loop de background: {e}")
    
    # Inicia o loop em uma thread separada (daemon=True para não travar o servidor)
    import threading
    thread = threading.Thread(target=run_background_loop, daemon=True)
    thread.start()
    print("✅ Loop de relatórios iniciado em background!")
    
    # ============================================
    # Inicia o servidor (NÃO DESLIGA MAIS)
    # ============================================
    
    port = int(os.environ.get("PORT", 8000))
    
    # 🔧 CORREÇÃO: Executa o servidor em um loop separado
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    
    # 🔧 CORREÇÃO: Inicia o servidor de forma síncrona (não desliga)
    server.run()

if __name__ == "__main__":
    main()
