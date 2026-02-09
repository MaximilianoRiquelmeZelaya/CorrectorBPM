import streamlit as st
import asana
from asana.rest import ApiException
import pandas as pd
import difflib 
import re 

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Corrector Asana", page_icon="✅", layout="wide")

# --- CONSTANTES ---
UMBRAL_APROBACION = 0.75
SECCION_PREDETERMINADA = "Inducción Ingreso Personal Nuevo/Contratista"

# --- CONFIGURACIÓN DE RESPUESTAS ---
RESPUESTAS_CORRECTAS = {
    # --- Selección Múltiple / Única ---
    '¿Cuál de las siguientes opciones está prohibida en las áreas de trabajo?': 'Todas las anteriores',
    '¿Qué condiciones de salud deben ser reportadas antes de ingresar a trabajar?': 'Todas las anteriores',
    '¿Cuál de las siguientes opciones es una falta a los requerimientos de presentación personal?': 'Uso de maquillaje y uñas largas',
    '¿Cuál es la acción correcta en caso de accidente/incidente en la planta?': 'Avisar de inmediato a mi Supervisor o Jefe de Área',
    
    # --- Verdadero o Falso ---
    'La inocuidad significa que el producto no causará daño a quien lo consuma.': 'Verdadero',
    'No es necesario lavarse las manos antes de ingresar a la planta.': 'Falso',
    'Un manipulador de alimentos es toda persona que trabaja en una planta de alimentos.': 'Verdadero',
    'En planta se puede comer siempre que se esté en un área separada.': 'Falso',
    'El maní y el huevo son alérgenos alimentarios.': 'Verdadero',
    'Está permitido limpiar mi cuerpo con aire comprimido, ya que no implica riesgos.': 'Falso',
    'La seguridad en la empresa es responsabilidad únicamente del supervisor.': 'Falso',
    'Los elementos de protección personal evitan los accidentes y no minimizan sus consecuencias.': 'Falso',
    'En caso de encontrar una condición insegura, es obligatorio informarlo a la jefatura.': 'Verdadero',
    'Se pueden realizar labores de limpieza interna en equipos en funcionamiento.': 'Falso',
    
    # --- Preguntas Abiertas (Sinónimos Agrupados) ---
    'Mencione tres prohibiciones de higiene y seguridad en la planta.': {
        "tipo": "texto_abierto",
        "minimo": 3,
        "palabras": [
            ['nariz'], ['oídos', 'oidos'], ['escupir'], 
            ['reloj'], ['ropa', 'vestimenta'], ['calle'], 
            ['joyas', 'anillos', 'aros'], ['cabello', 'pelo'], 
            ['perfume', 'maquillaje', 'cremas', 'olor'],
            ['fumar'], ['alimentos', 'comer', 'comida'], ['chicle'], ['medicamentos'],
            ['celular', 'teléfono'], ['audífonos'],
            ['bolsos', 'mochilas'], ['dinero', 'plata'], ['llaves'],
            ['animales'], ['aire', 'comprimido']
        ]
    },
    
    'Mencione tres riesgos a los que estará expuesto en la empresa.': {
        "tipo": "texto_abierto",
        "minimo": 3,
        "palabras": [
            ['cortes', 'corte'], 
            ['caídas', 'caidas', 'caída', 'caida'],
            ['atrapamiento', 'atrapado'],
            ['quemaduras', 'quemadura'],
            ['ruido'],
            ['eléctrico', 'electrico', 'electricidad', 'corriente'],
            ['incendios', 'incendio', 'fuego'],
            ['móviles', 'moviles', 'equipos', 'vehículos']
        ]
    }
}

# --- LÓGICA INTELIGENTE ---
def limpiar_texto(texto):
    if not isinstance(texto, str): return []
    return re.findall(r'\w+', texto.lower())

def validar_texto_inteligente(texto_usuario, grupos_palabras, minimo_requerido):
    if not texto_usuario: return False, []
    
    palabras_usuario = limpiar_texto(texto_usuario)
    conceptos_encontrados = []

    for grupo in grupos_palabras:
        if isinstance(grupo, str): grupo = [grupo]
        concepto_detectado = False
        
        for clave in grupo:
            clave_limpia = clave.lower()
            if clave_limpia in palabras_usuario:
                concepto_detectado = True
                break
            coincidencias = difflib.get_close_matches(clave_limpia, palabras_usuario, n=1, cutoff=0.85)
            if coincidencias:
                concepto_detectado = True
                break
        
        if concepto_detectado:
            conceptos_encontrados.append(grupo[0].upper())

    es_correcto = len(conceptos_encontrados) >= minimo_requerido
    return es_correcto, conceptos_encontrados

# --- AUXILIARES ---
def formatear_valor(valor):
    if isinstance(valor, list): return ", ".join(valor)
    if isinstance(valor, dict) and valor.get('tipo') == 'texto_abierto':
        ejemplos = [g[0] if isinstance(g, list) else g for g in valor['palabras'][:4]]
        return f"Mencionar {valor['minimo']} conceptos (ej: {', '.join(ejemplos)}...)"
    if valor is None: return "Sin respuesta"
    return str(valor)

def conectar_asana(token):
    configuration = asana.Configuration()
    configuration.access_token = token
    return asana.ApiClient(configuration)

def obtener_secciones(client, project_gid):
    try:
        sections_api = asana.SectionsApi(client)
        result = sections_api.get_sections_for_project(project_gid, {'opt_fields': 'name,gid'})
        secciones = list(result) if isinstance(result, list) else list(getattr(result, 'data', result))
        
        lista = []
        for s in secciones:
            s_dict = s.to_dict() if hasattr(s, 'to_dict') else s
            lista.append({'name': s_dict['name'], 'gid': s_dict['gid']})
        return lista
    except Exception as e:
        st.error(f"Error al cargar secciones: {e}")
        return []

def evaluar_tarea(task_custom_fields):
    puntos_obtenidos = 0
    total_preguntas = len(RESPUESTAS_CORRECTAS)
    errores = []
    observaciones_positivas = [] # NUEVA LISTA para guardar los aciertos con detalle

    campos_tarea = {}
    for field in task_custom_fields:
        nombre = field['name']
        valor = None
        if field.get('resource_subtype') == 'enum' and field.get('enum_value'):
            valor = field['enum_value']['name']
        elif field.get('resource_subtype') == 'multi_enum' and field.get('multi_enum_values'):
            valor = [v['name'] for v in field['multi_enum_values']]
        elif field.get('resource_subtype') == 'text':
            valor = field.get('text_value')
        campos_tarea[nombre] = valor

    for pregunta, criterio in RESPUESTAS_CORRECTAS.items():
        respuesta_usuario = campos_tarea.get(pregunta)
        es_correcto = False
        info_extra = "" 

        # Si no respondió
        if not respuesta_usuario:
            errores.append(f"⚠️ Pregunta '{pregunta}': No respondida.")
            continue

        # CASO A: Texto Abierto Inteligente
        if isinstance(criterio, dict) and criterio.get('tipo') == 'texto_abierto':
            pasa_validacion, aciertos = validar_texto_inteligente(
                str(respuesta_usuario), 
                criterio['palabras'], 
                criterio['minimo']
            )
            # Guardamos el estado SIEMPRE (para usarlo en acierto o error)
            info_extra = f"(Detectados: {len(aciertos)}/{criterio['minimo']})"
            es_correcto = pasa_validacion

        # CASO B: Selección Múltiple
        elif isinstance(criterio, list) and isinstance(respuesta_usuario, list):
            aciertos_set = set(criterio).intersection(set(respuesta_usuario))
            if len(aciertos_set) == len(criterio): es_correcto = True

        # CASO C: Texto Exacto / Selección Única
        elif isinstance(criterio, str):
            if isinstance(respuesta_usuario, str) and respuesta_usuario.strip() == criterio:
                es_correcto = True
            elif isinstance(respuesta_usuario, list) and len(respuesta_usuario) == 1:
                if respuesta_usuario[0] == criterio: es_correcto = True

        # Resultado
        msg_usuario = formatear_valor(respuesta_usuario)
        
        if es_correcto:
            puntos_obtenidos += 1
            # NUEVO: Si es pregunta abierta y está correcta, guardamos el detalle
            if info_extra:
                observaciones_positivas.append(
                    f"✅ Pregunta: {pregunta} {info_extra}\n"
                    f"   • Tu respuesta: {msg_usuario}"
                )
        else:
            msg_correcta = formatear_valor(criterio)
            
            # --- CAMBIO AQUÍ ---
            # Solo construimos la línea de estado si es una pregunta abierta (tiene info_extra)
            linea_estado = f"   • Estado: Incorrecta {info_extra}\n" if info_extra else ""
            
            errores.append(
                f"❌ Pregunta: {pregunta}\n"
                f"{linea_estado}" # Esta línea estará vacía en preguntas cerradas
                f"   • Tu respuesta: {msg_usuario}\n"
                f"   • Requisito: {msg_correcta}"
            )

    puntaje_final = puntos_obtenidos / total_preguntas if total_preguntas > 0 else 0
    return puntaje_final, errores, observaciones_positivas, puntos_obtenidos, total_preguntas


# --- APP ---
if 'tareas_cargadas' not in st.session_state: st.session_state.tareas_cargadas = []
if 'secciones_disponibles' not in st.session_state: st.session_state.secciones_disponibles = []

st.title("🤖 Corrector Automático Asana")

with st.sidebar:
    st.header("🔐 Configuración")
    token_input = st.text_input("PAT", type="password")
    gid_input = st.text_input("GID")
    
    if st.button("📂 Buscar Secciones"):
        if token_input and gid_input:
            client = conectar_asana(token_input)
            st.session_state.secciones_disponibles = obtener_secciones(client, gid_input)
            if st.session_state.secciones_disponibles: st.success("Listo.")
    
    opciones = [s['name'] for s in st.session_state.secciones_disponibles]
    idx = opciones.index(SECCION_PREDETERMINADA) if SECCION_PREDETERMINADA in opciones else 0
    seccion_sel = st.selectbox("Sección:", options=opciones, index=idx if opciones else 0, disabled=not opciones)
    
    st.divider()

    if st.button("🔄 Cargar Tareas", type="primary", disabled=not opciones):
        with st.spinner("Cargando..."):
            client = conectar_asana(token_input)
            tasks_api = asana.TasksApi(client)
            sec_gid = next(s['gid'] for s in st.session_state.secciones_disponibles if s['name'] == seccion_sel)
            
            opts = {
                'completed_since': 'now', 'section': sec_gid,
                'opt_fields': "name,completed,assignee.name,custom_fields.name,custom_fields.enum_value,custom_fields.text_value,custom_fields.resource_subtype,custom_fields.multi_enum_values"
            }
            result = tasks_api.get_tasks_for_section(sec_gid, opts)
            tasks = list(result) if isinstance(result, list) else list(getattr(result, 'data', result))
            
            lista = []
            for t in tasks:
                td = t.to_dict() if hasattr(t, 'to_dict') else t
                asig = td.get('assignee')
                lista.append({
                    'gid': td.get('gid'), 'name': td.get('name', 'Sin nombre'),
                    'assignee_name': asig['name'] if asig else "Sin Asignar",
                    'custom_fields': td.get('custom_fields', [])
                })
            st.session_state.tareas_cargadas = lista
            st.success(f"{len(lista)} tareas.")

if st.session_state.tareas_cargadas:
    st.subheader(f"📋 Revisión: {seccion_sel}")
    df = pd.DataFrame(st.session_state.tareas_cargadas)
    
    col1, col2 = st.columns([1,2])
    resps = sorted(df['assignee_name'].unique().tolist())
    filtro_resp = col1.multiselect("Responsable:", options=resps, default=resps)
    
    df_f = df[df['assignee_name'].isin(filtro_resp)].copy()
    if 'Seleccionar' not in df_f.columns: df_f.insert(0, "Seleccionar", True)
    
    edited = st.data_editor(
        df_f, hide_index=True, use_container_width=True,
        column_config={"gid": st.column_config.TextColumn("ID", disabled=True), "custom_fields": None},
        key=f"edit_{len(filtro_resp)}"
    )
    
    seleccionadas = edited[edited['Seleccionar'] == True]
    
    if st.button(f"🚀 Evaluar {len(seleccionadas)} tareas"):
        bar = st.progress(0)
        log = st.status("Procesando...", expanded=True)
        client = conectar_asana(token_input)
        tasks_api = asana.TasksApi(client)
        stories_api = asana.StoriesApi(client)
        ap, rp = 0, 0
        
        for i, gid in enumerate(seleccionadas['gid']):
            bar.progress((i+1)/len(seleccionadas))
            task = next((t for t in st.session_state.tareas_cargadas if t["gid"] == gid), None)
            
            if task:
                log.write(f"🔍 **{task['name']}** ({task['assignee_name']})")
                if 'custom_fields' in task:
                    # Obtenemos errores Y observaciones positivas
                    puntaje, errores, obs_positivas, puntos, total = evaluar_tarea(task['custom_fields'])
                    
                    txt_err = "\n".join(errores)
                    txt_pos = "\n".join(obs_positivas) # Texto de los aciertos detallados
                    
                    str_puntos = f"{puntos}/{total} pts"
                    
                    if puntaje >= UMBRAL_APROBACION:
                        ap += 1
                        # CAMBIO 3: Agregamos str_puntos al mensaje
                        msg = f"✅ Tarea Aprobada ({puntaje*100:.0f}% | {str_puntos})."
                        
                        detalles = ""
                        if errores: detalles += f"\n\n⚠️ Observaciones menores:\n{txt_err}"
                        if obs_positivas: detalles += f"\n\n📝 Detalle de respuestas abiertas:\n{txt_pos}"
                        
                        msg += detalles if detalles else " ¡Excelente trabajo, sin errores!"
                        
                        tasks_api.update_task(task_gid=gid, body={'data':{'completed':True}}, opts={})
                        stories_api.create_story_for_task(task_gid=gid, body={'data':{'text':msg}}, opts={})
                        
                        # CAMBIO 4: Lo mostramos en el log visual
                        log.write(f"&nbsp;&nbsp; ✅ APROBADO ({str_puntos})")
                    else:
                        rp += 1
                        # CAMBIO 5: Agregamos str_puntos al mensaje de reprobado
                        msg = f"🤖 Reprobado ({puntaje*100:.0f}% | {str_puntos}).\nSe requiere corrección:\n\n{txt_err}"
                        stories_api.create_story_for_task(task_gid=gid, body={'data':{'text':msg}}, opts={})
                        log.write(f"&nbsp;&nbsp; ❌ REPROBADO ({str_puntos})")
        
        log.update(label="Fin", state="complete")
        st.success(f"Aprobados: {ap} | Reprobados: {rp}")
else:
    st.info("Carga tareas.")
