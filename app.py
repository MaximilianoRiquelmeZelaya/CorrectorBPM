import streamlit as st
import asana
from asana.rest import ApiException
import time

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Corrector Asana", page_icon="✅", layout="wide")

# --- CONSTANTES ---
UMBRAL_APROBACION = 0.7  # 70%

# Diccionario de Respuestas Correctas
RESPUESTAS_CORRECTAS = {
    '1': '1',
    '2': '2',
    '3': '3',
    '4': ['1', '2', '3', '4'],
    '5': ['1', '2', '3', '4', '5'],
    '6': ['1', '2', '3', '4', '5', '6'],
    '7': ['1', '2', '3', '4', '5', '6', '7'],
    '8': '8',
    '9': '9',
    '10': '10'
}

# --- FUNCIONES AUXILIARES ---
def formatear_valor(valor):
    """Convierte listas o valores en texto limpio."""
    if isinstance(valor, list):
        return ", ".join(valor)
    if valor is None:
        return "Sin respuesta"
    return str(valor)

def conectar_asana(token):
    configuration = asana.Configuration()
    configuration.access_token = token
    return asana.ApiClient(configuration)

def evaluar_tarea(task_custom_fields):
    puntos_obtenidos = 0
    total_preguntas = len(RESPUESTAS_CORRECTAS)
    errores = []

    # Mapeo de campos
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

    # Evaluación
    for pregunta, respuesta_correcta in RESPUESTAS_CORRECTAS.items():
        respuesta_usuario = campos_tarea.get(pregunta)
        es_correcto = False

        if not respuesta_usuario:
            errores.append(f"⚠️ Pregunta '{pregunta}': No respondida (Correcta: {formatear_valor(respuesta_correcta)})")
            continue

        if isinstance(respuesta_correcta, str) and not isinstance(respuesta_usuario, list):
            if pregunta in ['8', '9', '10']: 
                if respuesta_correcta.lower() in respuesta_usuario.lower():
                    es_correcto = True
            elif respuesta_usuario == respuesta_correcta:
                es_correcto = True

        elif isinstance(respuesta_correcta, list) and isinstance(respuesta_usuario, list):
            aciertos = set(respuesta_correcta).intersection(set(respuesta_usuario))
            if len(aciertos) == len(respuesta_correcta):
                es_correcto = True

        if es_correcto:
            puntos_obtenidos += 1
        else:
            msg_usuario = formatear_valor(respuesta_usuario)
            msg_correcta = formatear_valor(respuesta_correcta)
            errores.append(
                f"❌ Pregunta '{pregunta}': Incorrecta.\n"
                f"   • Tu respuesta: {msg_usuario}\n"
                f"   • Correcta: {msg_correcta}"
            )

    puntaje_final = puntos_obtenidos / total_preguntas if total_preguntas > 0 else 0
    return puntaje_final, errores

# --- INTERFAZ PRINCIPAL ---
st.title("🤖 Corrector Automático de Asana")
st.markdown("Esta aplicación conecta con Asana, evalúa los formularios y cierra las tareas aprobadas o comenta en las reprobadas.")

with st.sidebar:
    st.header("🔐 Credenciales")
    token_input = st.text_input("Personal Access Token (PAT)", type="password", help="Tu token comienza con 1/ o 2/")
    gid_input = st.text_input("Project GID", help="El ID numérico del proyecto en la URL")
    
    st.divider()
    st.info("Asegúrate de que el usuario del Token tenga acceso al proyecto.")

# Botón de ejecución
if st.button("🚀 Ejecutar Corrección", type="primary", use_container_width=True):
    if not token_input or not gid_input:
        st.error("Por favor ingresa el Token y el GID del proyecto.")
    else:
        # Contenedores para resultados
        status_container = st.status("Iniciando conexión...", expanded=True)
        col1, col2, col3 = st.columns(3)
        aprobados = 0
        reprobados = 0
        ignorados = 0

        try:
            client = conectar_asana(token_input)
            tasks_api = asana.TasksApi(client)
            stories_api = asana.StoriesApi(client)

            status_container.write("✅ Conexión establecida.")
            status_container.write("📥 Obteniendo tareas abiertas...")

            opts = {
                'completed_since': 'now', 
                'opt_fields': "name,completed,custom_fields.name,custom_fields.enum_value,custom_fields.text_value,custom_fields.resource_subtype,custom_fields.multi_enum_values"
            }
            
            # Obtener tareas
            result = tasks_api.get_tasks_for_project(gid_input, opts)
            tasks = list(result) if isinstance(result, list) else list(getattr(result, 'data', result))

            if not tasks:
                status_container.update(label="No se encontraron tareas pendientes.", state="complete")
                st.warning("No hay tareas abiertas en este proyecto para evaluar.")
            else:
                total_tasks = len(tasks)
                progress_bar = st.progress(0)
                
                for i, task in enumerate(tasks):
                    # Actualizar barra de progreso
                    progress_bar.progress((i + 1) / total_tasks)
                    
                    task_data = task.to_dict() if hasattr(task, 'to_dict') else task
                    nombre_tarea = task_data.get('name', 'Tarea sin nombre')
                    
                    status_container.write(f"🔍 Evaluando: **{nombre_tarea}**")
                    
                    if 'custom_fields' in task_data:
                        puntaje, lista_errores = evaluar_tarea(task_data['custom_fields'])
                        texto_errores = "\n".join(lista_errores)
                        reporte = f"\n\n🔍 Detalle de errores:\n{texto_errores}" if lista_errores else ""

                        if puntaje >= UMBRAL_APROBACION:
                            # APROBADO
                            aprobados += 1
                            msg_log = f"✅ APROBADO ({puntaje*100:.1f}%)"
                            
                            if lista_errores:
                                mensaje = f"✅ Tarea Aprobada ({puntaje*100:.1f}%).{reporte}"
                                stories_api.create_story_for_task(
                                    task_gid=task_data['gid'], 
                                    body={'data': {'text': mensaje}}, 
                                    opts={}
                                )
                            
                            tasks_api.update_task(
                                task_gid=task_data['gid'], 
                                body={'data': {'completed': True}}, 
                                opts={}
                            )
                        else:
                            # REPROBADO
                            reprobados += 1
                            msg_log = f"❌ REPROBADO ({puntaje*100:.1f}%)"
                            mensaje = (
                                f"🤖 Evaluación Automática: Reprobado ({puntaje*100:.1f}%).\n"
                                f"Se requiere corrección.{reporte}"
                            )
                            stories_api.create_story_for_task(
                                task_gid=task_data['gid'], 
                                body={'data': {'text': mensaje}}, 
                                opts={}
                            )
                        
                        status_container.write(f"&nbsp;&nbsp;&nbsp;&nbsp; -> {msg_log}")

                    else:
                        ignorados += 1
                        status_container.write(f"&nbsp;&nbsp;&nbsp;&nbsp; -> ⚠️ Ignorada (Sin campos)")

                status_container.update(label="✅ Proceso finalizado", state="complete")

                # Mostrar Métricas Finales
                col1.metric("Aprobados", aprobados)
                col2.metric("Reprobados", reprobados)
                col3.metric("Ignorados", ignorados)

                if aprobados + reprobados > 0:
                    st.success("¡Evaluación completada con éxito!")

        except ApiException as e:
            status_container.update(label="❌ Error de API", state="error")
            if e.status == 401:
                st.error("Error 401: Token inválido o no autorizado. Verifica tus credenciales.")
            elif e.status == 404:
                st.error("Error 404: Proyecto no encontrado. Verifica el GID.")
            else:
                st.error(f"Error de Asana: {e}")
        except Exception as e:
            status_container.update(label="❌ Error inesperado", state="error")
            st.error(f"Ocurrió un error: {e}")
