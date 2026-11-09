from datetime import datetime
import os
import sys
import threading
import subprocess
import webbrowser
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
    session,
)
from functools import wraps
from werkzeug.utils import secure_filename
import sqlite3
import json
import requests
#import webview  # Importamos pywebview para la ventana nativa

# Configuración de rutas corregida para que lea siempre la carpeta templates correctamente
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
    template_folder = os.path.join(sys._MEIPASS, 'templates')
    static_folder = os.path.join(sys._MEIPASS, 'static')
    app = Flask(__name__, template_folder=template_folder, static_folder=static_folder)
else:
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    template_dir = os.path.join(BASE_DIR, 'templates')
    app = Flask(__name__, template_folder=template_dir)

app.secret_key = os.getenv("SECRET_KEY", "ferreteria_marlon_secret_key_2026")

UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

APIS_NET_PE_TOKEN = os.getenv("APIS_NET_PE_TOKEN", "apis-token-1.aT0D-y2557.70298")

CREDENCIALES_SUCURSALES = {
    "admin": "123456",
    "tienda2": "123456"
}

DATOS_SUCURSALES = {
    "admin": {
        "direccion": "MZ F17 LT 45 MARISCAL CACERES CON AV WISSE - SAN JUAN DE LURIGANCHO - Lima",
        "telefono": "926880283",
        "db": "ferreteria_admin.db"
    },
    "tienda2": {
        "direccion": "AV. PACHACÚTEC - MERCADO LA FORTUNA - LT 7 PST 20 - PORTON",
        "telefono": "926880283",
        "db": "ferreteria_tienda2.db"
    }
}


# Persistencia local de usuarios y contraseñas configurables.
CREDENTIALS_FILE = os.path.join(BASE_DIR, "usuarios_config.json")
def cargar_credenciales_guardadas():
    global CREDENCIALES_SUCURSALES, DATOS_SUCURSALES
    try:
        with open(CREDENTIALS_FILE, "r", encoding="utf-8") as f:
            guardados = json.load(f)
        for usuario, datos in guardados.items():
            if isinstance(datos, dict) and "db" in datos:
                DATOS_SUCURSALES[usuario] = {"db": datos["db"], "direccion": datos.get("direccion", ""), "telefono": datos.get("telefono", "")}
                CREDENCIALES_SUCURSALES[usuario] = datos.get("password", "123456")
            elif usuario in DATOS_SUCURSALES and isinstance(datos, dict):
                CREDENCIALES_SUCURSALES[usuario] = datos.get("password", CREDENCIALES_SUCURSALES.get(usuario, "123456"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass

def guardar_credenciales():
    datos = {}
    for usuario, password in CREDENCIALES_SUCURSALES.items():
        sucursal = DATOS_SUCURSALES.get(usuario, DATOS_SUCURSALES.get("admin", {}))
        datos[usuario] = {"password": password, "db": sucursal.get("db", "ferreteria_admin.db"), "direccion": sucursal.get("direccion", ""), "telefono": sucursal.get("telefono", "")}
    temporal = CREDENTIALS_FILE + ".tmp"
    with open(temporal, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)
    os.replace(temporal, CREDENTIALS_FILE)

cargar_credenciales_guardadas()


def get_db_connection():
  username = session.get("username", "admin")
  config_sucursal = DATOS_SUCURSALES.get(username, DATOS_SUCURSALES["admin"])
  db_name = config_sucursal["db"]
  db_path = os.path.join(BASE_DIR, db_name)
  conn = sqlite3.connect(db_path, timeout=10)
  conn.row_factory = sqlite3.Row
  return conn


def init_db_for_all():
  for user, data in DATOS_SUCURSALES.items():
    db_file = os.path.join(BASE_DIR, data["db"])
    with sqlite3.connect(db_file) as conn:
      cursor = conn.cursor()

      cursor.execute("""
                CREATE TABLE IF NOT EXISTS productos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nombre TEXT NOT NULL,
                    proveedor TEXT,
                    precio_compra REAL NOT NULL,
                    precio_venta REAL NOT NULL,
                    precio REAL,
                    stock INTEGER NOT NULL,
                    imagen TEXT
                )
            """)

      cursor.execute("""
                CREATE TABLE IF NOT EXISTS ventas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    cliente TEXT NOT NULL,
                    documento TEXT,
                    tipo_comprobante TEXT DEFAULT 'NOTA',
                    serie TEXT,
                    correlativo INTEGER,
                    comprobante_numero TEXT,
                    total REAL NOT NULL,
                    metodo_pago TEXT DEFAULT 'Sin registrar',
                    enviado_sunat INTEGER DEFAULT 0
                )
            """)

      try:
          cursor.execute("ALTER TABLE ventas ADD COLUMN serie TEXT")
      except sqlite3.OperationalError:
          pass
      try:
          cursor.execute("ALTER TABLE ventas ADD COLUMN correlativo INTEGER")
      except sqlite3.OperationalError:
          pass
      try:
          cursor.execute("ALTER TABLE ventas ADD COLUMN comprobante_numero TEXT")
      except sqlite3.OperationalError:
          pass
      try:
          cursor.execute("ALTER TABLE ventas ADD COLUMN metodo_pago TEXT DEFAULT 'Sin registrar'")
      except sqlite3.OperationalError:
          pass

      cursor.execute("""
                CREATE TABLE IF NOT EXISTS detalle_ventas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    venta_id INTEGER,
                    producto_id INTEGER,
                    cantidad INTEGER,
                    precio_unitario REAL,
                    subtotal REAL,
                    FOREIGN KEY (venta_id) REFERENCES ventas (id),
                    FOREIGN KEY (producto_id) REFERENCES productos (id)
                )
            """)

      # Migración segura: conserva los datos existentes y guarda el costo histórico por línea vendida.
      columnas_detalle = {fila[1] for fila in cursor.execute("PRAGMA table_info(detalle_ventas)").fetchall()}
      if "precio_compra_unitario" not in columnas_detalle:
          cursor.execute("ALTER TABLE detalle_ventas ADD COLUMN precio_compra_unitario REAL DEFAULT 0")

      cursor.execute("""
                CREATE TABLE IF NOT EXISTS proformas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    cliente TEXT NOT NULL,
                    documento TEXT,
                    total REAL NOT NULL
                )
            """)

      cursor.execute("""
                CREATE TABLE IF NOT EXISTS detalle_proforma (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    proforma_id INTEGER,
                    producto_id INTEGER,
                    cantidad INTEGER,
                    precio_unitario REAL,
                    subtotal REAL,
                    FOREIGN KEY (proforma_id) REFERENCES proformas (id),
                    FOREIGN KEY (producto_id) REFERENCES productos (id)
                )
            """)

      cursor.execute("""
                CREATE TABLE IF NOT EXISTS proveedores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ruc TEXT UNIQUE,
                    razon_social TEXT NOT NULL,
                    telefono TEXT,
                    contacto TEXT,
                    direccion TEXT
                )
            """)

      cursor.execute("""
                CREATE TABLE IF NOT EXISTS compras_proveedores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    nro_documento TEXT NOT NULL,
                    proveedor TEXT NOT NULL,
                    imagen_general TEXT,
                    total REAL NOT NULL
                )
            """)

      cursor.execute("""
                CREATE TABLE IF NOT EXISTS detalle_compras (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    compra_id INTEGER,
                    producto_nombre TEXT,
                    cantidad INTEGER,
                    precio_compra REAL,
                    precio_venta REAL,
                    imagen TEXT,
                    FOREIGN KEY (compra_id) REFERENCES compras_proveedores (id)
                )
            """)

      # Gastos diarios: se guardan por sucursal y se descuentan del arqueo según el medio de pago.
      cursor.execute("""
          CREATE TABLE IF NOT EXISTS gastos (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              fecha TEXT NOT NULL,
              descripcion TEXT NOT NULL,
              categoria TEXT DEFAULT 'General',
              metodo_pago TEXT NOT NULL DEFAULT 'Efectivo',
              monto REAL NOT NULL CHECK (monto > 0)
          )
      """)

      # Migración segura: agrega campos nuevos sin borrar compras existentes.
      columnas_compras = {fila[1] for fila in cursor.execute("PRAGMA table_info(compras_proveedores)").fetchall()}
      if "tipo_comprobante" not in columnas_compras:
          cursor.execute("ALTER TABLE compras_proveedores ADD COLUMN tipo_comprobante TEXT DEFAULT 'Nota/Ticket'")
      if "ruc" not in columnas_compras:
          cursor.execute("ALTER TABLE compras_proveedores ADD COLUMN ruc TEXT")
      if "metodo_pago" not in columnas_compras:
          cursor.execute("ALTER TABLE compras_proveedores ADD COLUMN metodo_pago TEXT DEFAULT 'Efectivo'")
      conn.commit()


init_db_for_all()


def login_required(f):
  @wraps(f)
  def decorated_function(*args, **kwargs):
    if "user_id" not in session:
      return redirect(url_for("login"))
    return f(*args, **kwargs)
  return decorated_function


@app.route("/login", methods=["GET", "POST"])
def login():
  if request.method == "POST":
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()

    if username in DATOS_SUCURSALES:
      if username in CREDENCIALES_SUCURSALES and password == CREDENCIALES_SUCURSALES[username]:
        session["user_id"] = 1 if username == "admin" else 2
        session["username"] = username
        session["sucursal"] = username
        flash("¡Bienvenido al sistema!", "success")
        return redirect(url_for("index"))

    flash("Usuario o contraseña incorrectos.", "error")

  return render_template("login.html")


@app.route("/cambiar-password", methods=["GET", "POST"])
@app.route("/cambiar_password", methods=["GET", "POST"])
@login_required
def cambiar_password():
  username = session.get("username")
  
  if request.method == "POST":
    password_actual = request.form.get("password_actual", "").strip()
    nuevo_password = request.form.get("nuevo_password", "").strip()
    confirmar_password = request.form.get("confirmar_password", "").strip()

    if CREDENCIALES_SUCURSALES.get(username) == password_actual:
      if not nuevo_password:
        flash("La nueva contraseña no puede estar vacía.", "error")
      elif nuevo_password != confirmar_password:
        flash("Las nuevas contraseñas no coinciden.", "error")
      else:
        CREDENCIALES_SUCURSALES[username] = nuevo_password
        guardar_credenciales()
        flash("¡Contraseña actualizada con éxito!", "success")
        return redirect(url_for("index"))
    else:
      flash("La contraseña actual es incorrecta.", "error")

  return render_template("cambiar_password.html")


@app.route("/guardar-configuracion-usuario", methods=["POST"])
@login_required
def guardar_configuracion_usuario():
    datos = request.get_json(silent=True) or request.form
    actual = session.get("username", "admin")
    password_actual = str(datos.get("password_actual", "")).strip()
    nuevo_usuario = str(datos.get("nuevo_usuario", actual)).strip()
    nuevo_password = str(datos.get("nuevo_password", "")).strip()
    if CREDENCIALES_SUCURSALES.get(actual) != password_actual:
        return jsonify({"ok": False, "mensaje": "La contraseña actual no es correcta."}), 400
    if not nuevo_usuario or len(nuevo_usuario) < 3:
        return jsonify({"ok": False, "mensaje": "El usuario debe tener al menos 3 caracteres."}), 400
    if nuevo_usuario != actual and nuevo_usuario in DATOS_SUCURSALES:
        return jsonify({"ok": False, "mensaje": "Ese usuario ya existe."}), 400
    if nuevo_password and len(nuevo_password) < 6:
        return jsonify({"ok": False, "mensaje": "La contraseña nueva debe tener al menos 6 caracteres."}), 400
    # Renombrar la cuenta manteniendo su sucursal/base de datos asociada.
    if nuevo_usuario != actual:
        CREDENCIALES_SUCURSALES[nuevo_usuario] = CREDENCIALES_SUCURSALES.pop(actual)
        DATOS_SUCURSALES[nuevo_usuario] = DATOS_SUCURSALES.pop(actual)
        session["username"] = nuevo_usuario
        session["sucursal"] = nuevo_usuario
    if nuevo_password:
        CREDENCIALES_SUCURSALES[nuevo_usuario] = nuevo_password
    guardar_credenciales()
    return jsonify({"ok": True, "mensaje": "Configuración de usuario guardada."})


@app.route("/logout")
def logout():
  session.clear()
  flash("Sesión cerrada correctamente.", "info")
  return redirect(url_for("login"))


def consultar_dni_gratis(dni):
  try:
    url = f"https://api.apis.net.pe/v1/dni?numero={dni}"
    headers = {"Authorization": f"Bearer {APIS_NET_PE_TOKEN}"}
    r = requests.get(url, headers=headers, timeout=3)
    if r.status_code == 200:
      data = r.json()
      nombre = data.get("nombre")
      if nombre:
        return nombre.strip()
  except Exception as e:
    print("Error DNI:", e)
  return None


def consultar_ruc_gratis(ruc):
  try:
    url = f"https://api.apis.net.pe/v1/ruc?numero={ruc}"
    headers = {"Authorization": f"Bearer {APIS_NET_PE_TOKEN}"}
    r = requests.get(url, headers=headers, timeout=3)
    if r.status_code == 200:
      data = r.json()
      nombre = data.get("nombre")
      if nombre:
        return nombre.strip()
  except Exception as e:
    print("Error RUC:", e)
  return None


@app.route("/static/uploads/<filename>")
def uploaded_file(filename):
  return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


@app.route("/")
@login_required
def index():
  fecha_desde = (request.args.get("desde") or "").strip()
  fecha_hasta = (request.args.get("hasta") or "").strip()
  fecha_inicio = (request.args.get("fecha_inicio") or datetime.now().strftime("%Y-%m-%d")).strip()
  try:
    datetime.strptime(fecha_inicio, "%Y-%m-%d")
  except ValueError:
    fecha_inicio = datetime.now().strftime("%Y-%m-%d")
  # Validación básica de formato para evitar consultas con fechas inválidas.
  for valor in (fecha_desde, fecha_hasta):
    if valor:
      try:
        datetime.strptime(valor, "%Y-%m-%d")
      except ValueError:
        if valor == fecha_desde:
          fecha_desde = ""
        if valor == fecha_hasta:
          fecha_hasta = ""
  with get_db_connection() as conn:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, nombre, proveedor, precio_compra, precio_venta, stock, imagen FROM productos ORDER BY id DESC"
    )
    productos = cursor.fetchall()

    ganancia_estimada = sum([
        (p["precio_venta"] - p["precio_compra"]) * p["stock"] for p in productos
    ])

    hoy_str = fecha_inicio
    cursor.execute(
        """
        SELECT SUM(
            CASE 
                WHEN v.total > 0 THEN v.total 
                ELSE COALESCE((SELECT SUM(COALESCE(dv.subtotal, dv.cantidad * COALESCE(p.precio_venta, p.precio, 0))) 
                               FROM detalle_ventas dv 
                               LEFT JOIN productos p ON dv.producto_id = p.id 
                               WHERE dv.venta_id = v.id), 0.0) 
            END
        ) 
        FROM ventas v 
        WHERE DATE(v.fecha) = DATE(?)
          AND UPPER(COALESCE(v.tipo_comprobante, '')) NOT IN ('PROFORMA', 'COTIZACION', 'COTIZACIÓN')
        """, 
        (hoy_str,)
    )
    caja_hoy = cursor.fetchone()[0] or 0.0

    cursor.execute("SELECT SUM(stock * precio_compra) FROM productos")
    res_val_inv = cursor.fetchone()[0]
    valor_inventario = res_val_inv if res_val_inv else 0.0

    cursor.execute("SELECT COUNT(*) FROM productos WHERE stock <= 5")
    stock_bajo_count = cursor.fetchone()[0] or 0

    mes_actual = datetime.now().strftime("%Y-%m")
    cursor.execute(
        """
        SELECT SUM(
            CASE 
                WHEN v.total > 0 THEN v.total 
                ELSE COALESCE((SELECT SUM(COALESCE(dv.subtotal, dv.cantidad * COALESCE(p.precio_venta, p.precio, 0))) 
                               FROM detalle_ventas dv 
                               LEFT JOIN productos p ON dv.producto_id = p.id 
                               WHERE dv.venta_id = v.id), 0.0) 
            END
        ) 
        FROM ventas v 
        WHERE v.fecha LIKE ?
        """, 
        (f"{mes_actual}%",)
    )
    res_ventas_mes = cursor.fetchone()[0]
    ventas_mes_total = float(res_ventas_mes or 0.0)
    cursor.execute("""
        SELECT COALESCE(SUM(dv.cantidad * (dv.precio_unitario -
            CASE WHEN COALESCE(dv.precio_compra_unitario, 0) > 0
                 THEN dv.precio_compra_unitario ELSE COALESCE(p.precio_compra, 0) END)), 0)
        FROM detalle_ventas dv
        JOIN ventas v ON v.id = dv.venta_id
        LEFT JOIN productos p ON p.id = dv.producto_id
        WHERE v.fecha LIKE ?
    """, (f"{mes_actual}%",))
    ganancia_mes = float(cursor.fetchone()[0] or 0.0)

    cursor.execute(
        """
        SELECT v.id, v.fecha, v.cliente, 
            CASE WHEN v.total > 0 THEN v.total ELSE 0.0 END AS total,
            v.tipo_comprobante, v.comprobante_numero
        FROM ventas v ORDER BY v.fecha DESC LIMIT 10
        """
    )
    ventas = cursor.fetchall()

    sql_boletas = "SELECT id, fecha, cliente, total, comprobante_numero FROM ventas WHERE UPPER(tipo_comprobante) = 'BOLETA'"
    params_boletas = []
    if fecha_desde:
      sql_boletas += " AND DATE(fecha) >= DATE(?)"
      params_boletas.append(fecha_desde)
    if fecha_hasta:
      sql_boletas += " AND DATE(fecha) <= DATE(?)"
      params_boletas.append(fecha_hasta)
    sql_boletas += " ORDER BY fecha DESC"
    cursor.execute(sql_boletas, params_boletas)
    boletas = cursor.fetchall()

    cursor.execute("SELECT id, fecha, cliente, total, comprobante_numero FROM ventas WHERE UPPER(tipo_comprobante) = 'FACTURA' ORDER BY fecha DESC")
    facturas = cursor.fetchall()

    cursor.execute("SELECT id, fecha, cliente, total, comprobante_numero FROM ventas WHERE UPPER(tipo_comprobante) IN ('NOTA', 'NOTA DE VENTA') ORDER BY fecha DESC")
    notas_venta = cursor.fetchall()

    cursor.execute("SELECT id, fecha, cliente, total FROM proformas ORDER BY id DESC")
    proformas = cursor.fetchall()

    cursor.execute("SELECT id, fecha, nro_documento, proveedor, imagen_general, total, tipo_comprobante, ruc, metodo_pago FROM compras_proveedores ORDER BY id DESC")
    compras_proveedores = cursor.fetchall()
    cursor.execute("SELECT COALESCE(SUM(total), 0) FROM compras_proveedores")
    total_compras_registradas = float(cursor.fetchone()[0] or 0)

    # Reportes por rango; si no se selecciona rango, se muestra el mes actual.
    if not fecha_desde and not fecha_hasta:
      fecha_desde = datetime.now().strftime("%Y-%m-01")
      fecha_hasta = datetime.now().strftime("%Y-%m-%d")
    sql_reporte = "SELECT COALESCE(SUM(CASE WHEN total > 0 THEN total ELSE 0 END), 0) FROM ventas WHERE 1=1"
    params_reporte = []
    if fecha_desde:
      sql_reporte += " AND DATE(fecha) >= DATE(?)"
      params_reporte.append(fecha_desde)
    if fecha_hasta:
      sql_reporte += " AND DATE(fecha) <= DATE(?)"
      params_reporte.append(fecha_hasta)
    cursor.execute(sql_reporte, params_reporte)
    ventas_periodo = float(cursor.fetchone()[0] or 0)
    sql_ganancia = """
        SELECT COALESCE(SUM(dv.cantidad * (dv.precio_unitario -
            CASE WHEN COALESCE(dv.precio_compra_unitario, 0) > 0
                 THEN dv.precio_compra_unitario ELSE COALESCE(p.precio_compra, 0) END)), 0)
        FROM detalle_ventas dv
        JOIN ventas v ON v.id = dv.venta_id
        LEFT JOIN productos p ON p.id = dv.producto_id
        WHERE 1=1
    """
    params_ganancia = []
    if fecha_desde:
      sql_ganancia += " AND DATE(v.fecha) >= DATE(?)"
      params_ganancia.append(fecha_desde)
    if fecha_hasta:
      sql_ganancia += " AND DATE(v.fecha) <= DATE(?)"
      params_ganancia.append(fecha_hasta)
    cursor.execute(sql_ganancia, params_ganancia)
    ganancia_periodo = float(cursor.fetchone()[0] or 0.0)
    costo_periodo = ventas_periodo - ganancia_periodo
    porcentaje_sobre_ventas = (ganancia_periodo / ventas_periodo * 100) if ventas_periodo else 0.0
    porcentaje_sobre_costo = (ganancia_periodo / costo_periodo * 100) if costo_periodo > 0 else 0.0

    anio_grafico = request.args.get("anio", str(datetime.now().year))
    try:
      anio_grafico = int(anio_grafico)
      if anio_grafico < 2000 or anio_grafico > 2100: raise ValueError
    except (TypeError, ValueError):
      anio_grafico = datetime.now().year
    nombres_meses = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
    cursor.execute("""
        SELECT CAST(strftime('%m', fecha) AS INTEGER) AS mes,
               SUM(CASE WHEN total > 0 THEN total ELSE 0 END) AS total_mes
        FROM ventas WHERE strftime('%Y', fecha) = ? GROUP BY strftime('%m', fecha)
    """, (str(anio_grafico),))
    ventas_por_mes = {int(row["mes"]): float(row["total_mes"] or 0) for row in cursor.fetchall()}
    ventas_mensuales = [{"mes": nombre, "ventas": ventas_por_mes.get(i, 0.0)} for i, nombre in enumerate(nombres_meses, 1)]
    max_ventas_mensuales = max([fila["ventas"] for fila in ventas_mensuales] or [0]) or 1

  return render_template(
      "index.html",
      productos=productos,
      ventas=ventas,
      boletas=boletas,
      facturas=facturas,
      notas_venta=notas_venta,
      proformas=proformas,
       compras_proveedores=compras_proveedores,
       total_compras_registradas=total_compras_registradas,
       ganancia_estimada=ganancia_estimada,
      caja_hoy=caja_hoy,
      fecha_inicio=fecha_inicio,
      valor_inventario=valor_inventario,
      stock_bajo_count=stock_bajo_count,
      ganancia_mes=ganancia_mes,
      fecha_desde=fecha_desde,
      fecha_hasta=fecha_hasta,
      ventas_periodo=ventas_periodo,
      ganancia_periodo=ganancia_periodo,
      costo_periodo=costo_periodo,
      porcentaje_sobre_ventas=porcentaje_sobre_ventas,
      porcentaje_sobre_costo=porcentaje_sobre_costo,
      anio_grafico=anio_grafico,
      ventas_mensuales=ventas_mensuales,
      max_ventas_mensuales=max_ventas_mensuales,
  )


@app.route("/agregar", methods=["POST"])
@login_required
def agregar():
  nombre = request.form["nombre"]
  proveedor = request.form.get("proveedor", "")
  precio_compra = float(request.form.get("precio_compra", 0))
  precio_venta = float(request.form.get("precio_venta", 0))
  stock = int(request.form.get("stock", 0))

  filename = None
  if "imagen" in request.files:
    file = request.files["imagen"]
    if file and file.filename != "":
      filename = secure_filename(file.filename)
      filename = f"{int(datetime.now().timestamp())}_{filename}"
      file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))

  with get_db_connection() as conn:
    cursor = conn.cursor()
    cursor.execute(
        """
            INSERT INTO productos (nombre, proveedor, precio_compra, precio_venta, precio, stock, imagen)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (nombre, proveedor, precio_compra, precio_venta, precio_venta, stock, filename),
    )
    conn.commit()

  return redirect(url_for("index"))


@app.route("/editar/<int:id>", methods=["POST"])
@login_required
def editar(id):
  nombre = request.form["nombre"]
  proveedor = request.form.get("proveedor", "")
  precio_compra = float(request.form.get("precio_compra", 0))
  precio_venta = float(request.form.get("precio_venta", 0))
  stock = int(request.form.get("stock", 0))

  with get_db_connection() as conn:
    cursor = conn.cursor()
    if "imagen" in request.files and request.files["imagen"].filename != "":
      file = request.files["imagen"]
      filename = secure_filename(file.filename)
      filename = f"{int(datetime.now().timestamp())}_{filename}"
      file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
      cursor.execute(
          """
                UPDATE productos
                SET nombre = ?, proveedor = ?, precio_compra = ?, precio_venta = ?, precio = ?, stock = ?, imagen = ?
                WHERE id = ?
            """,
          (nombre, proveedor, precio_compra, precio_venta, precio_venta, stock, filename, id),
      )
    else:
      cursor.execute(
          """
                UPDATE productos
                SET nombre = ?, proveedor = ?, precio_compra = ?, precio_venta = ?, precio = ?, stock = ?
                WHERE id = ?
            """,
          (nombre, proveedor, precio_compra, precio_venta, precio_venta, stock, id),
      )
    conn.commit()

  return redirect(url_for("index"))


@app.route("/eliminar/<int:id>")
@login_required
def eliminar(id):
  with get_db_connection() as conn:
    cursor = conn.cursor()
    cursor.execute("DELETE FROM productos WHERE id = ?", (id,))
    conn.commit()
  return redirect(url_for("index"))


@app.route("/proveedores")
@login_required
def proveedores():
  with get_db_connection() as conn:
    proveedores = conn.execute("SELECT * FROM proveedores ORDER BY razon_social ASC").fetchall()
  return render_template("proveedores.html", proveedores=proveedores)


@app.route("/agregar_proveedor", methods=["POST"])
@login_required
def agregar_proveedor():
  ruc = request.form.get("ruc", "").strip()
  razon_social = request.form.get("razon_social", "").strip()
  telefono = request.form.get("telefono", "").strip()
  contacto = request.form.get("contacto", "").strip()
  direccion = request.form.get("direccion", "").strip()

  if razon_social:
    with get_db_connection() as conn:
      try:
        conn.execute(
            """
                    INSERT INTO proveedores (ruc, razon_social, telefono, contacto, direccion)
                    VALUES (?, ?, ?, ?, ?)
                """,
            (ruc if ruc else None, razon_social, telefono, contacto, direccion),
        )
        conn.commit()
        flash("Proveedor registrado correctamente.", "success")
      except sqlite3.IntegrityError:
        flash("El RUC ya se encuentra registrado.", "error")

  return redirect(url_for("proveedores"))


@app.route("/eliminar_proveedor/<int:id>", methods=["POST"])
@login_required
def eliminar_proveedor(id):
  with get_db_connection() as conn:
    conn.execute("DELETE FROM proveedores WHERE id = ?", (id,))
    conn.commit()
    flash("Proveedor eliminado.", "info")
  return redirect(url_for("proveedores"))


@app.route("/guardar_compra_proveedor", methods=["POST"])
@login_required
def guardar_compra_proveedor():
    """Guarda comprobante y detalle en SQLite, actualizando stock en la misma transacción."""
    try:
        proveedor = request.form.get("proveedor", "").strip() or "PROVEEDOR GENERAL"
        nro_documento = request.form.get("nro_documento", "").strip()
        tipo_comprobante = request.form.get("tipo_comprobante", "Nota/Ticket").strip() or "Nota/Ticket"
        ruc = request.form.get("ruc", "").strip()
        metodo_pago = request.form.get("metodo_pago", "Efectivo").strip() or "Efectivo"

        if not nro_documento:
            return jsonify({"status": "error", "message": "Ingresa el número de comprobante."}), 400

        # Leer todos los productos enviados por el formulario.
        items = []
        i = 0
        while request.form.get(f"productos[{i}][nombre]") is not None:
            nombre = request.form.get(f"productos[{i}][nombre]", "").strip()
            if not nombre:
                break
            try:
                cant = int(request.form.get(f"productos[{i}][cant]", "1"))
                p_compra = float(request.form.get(f"productos[{i}][precio_compra]", "0"))
                p_venta = float(request.form.get(f"productos[{i}][precio_venta]", "0") or 0)
            except (TypeError, ValueError):
                return jsonify({"status": "error", "message": f"Cantidad o precio inválido en el producto {nombre}."}), 400
            if cant < 1 or p_compra <= 0 or p_venta < 0:
                return jsonify({"status": "error", "message": f"Revisa la cantidad y los precios de {nombre}."}), 400
            img = request.files.get(f"productos_img_{i}")
            items.append({"nombre": nombre, "cant": cant, "precio_compra": p_compra,
                          "precio_venta": p_venta, "imagen": img})
            i += 1

        if not items:
            return jsonify({"status": "error", "message": "Agrega al menos un producto a la compra."}), 400

        img_general = request.files.get("imagen_general")
        nombre_img_general = None
        if img_general and img_general.filename:
            filename = secure_filename(img_general.filename)
            nombre_img_general = f"{int(datetime.now().timestamp())}_gen_{filename}"
            img_general.save(os.path.join(app.config["UPLOAD_FOLDER"], nombre_img_general))

        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Evita que una doble pulsación vuelva a sumar la misma factura al stock.
            cursor.execute(
                "SELECT id FROM compras_proveedores WHERE nro_documento = ? AND proveedor = ? LIMIT 1",
                (nro_documento, proveedor)
            )
            if cursor.fetchone():
                return jsonify({"status": "error", "message": "Ya existe una compra con ese comprobante para este proveedor. Verifica el número antes de volver a guardarla."}), 409

            cursor.execute("""
                INSERT INTO compras_proveedores
                    (fecha, nro_documento, proveedor, imagen_general, total, tipo_comprobante, ruc, metodo_pago)
                VALUES (datetime('now', 'localtime'), ?, ?, ?, 0, ?, ?, ?)
            """, (nro_documento, proveedor, nombre_img_general, tipo_comprobante, ruc, metodo_pago))
            compra_id = cursor.lastrowid
            total_compra = 0.0

            for idx, item in enumerate(items):
                nombre = item["nombre"]
                cant = item["cant"]
                p_compra = item["precio_compra"]
                p_venta = item["precio_venta"]
                subtotal_item = cant * p_compra
                total_compra += subtotal_item

                prod_img = item["imagen"]
                nombre_prod_img = None
                if prod_img and prod_img.filename:
                    filename_prod = secure_filename(prod_img.filename)
                    nombre_prod_img = f"{int(datetime.now().timestamp())}_prod_{idx}_{filename_prod}"
                    prod_img.save(os.path.join(app.config["UPLOAD_FOLDER"], nombre_prod_img))

                cursor.execute("""
                    INSERT INTO detalle_compras
                        (compra_id, producto_nombre, cantidad, precio_compra, precio_venta, imagen)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (compra_id, nombre, cant, p_compra, p_venta, nombre_prod_img))

                # Busca sin distinguir mayúsculas/minúsculas ni espacios externos.
                cursor.execute("SELECT id, stock FROM productos WHERE LOWER(TRIM(nombre)) = LOWER(TRIM(?)) LIMIT 1", (nombre,))
                producto_existente = cursor.fetchone()
                if producto_existente:
                    nuevo_stock = int(producto_existente["stock"] or 0) + cant
                    if p_venta > 0:
                        if nombre_prod_img:
                            cursor.execute("""UPDATE productos SET stock=?, precio_compra=?, precio_venta=?, precio=?, proveedor=?, imagen=? WHERE id=?""",
                                           (nuevo_stock, p_compra, p_venta, p_venta, proveedor, nombre_prod_img, producto_existente["id"]))
                        else:
                            cursor.execute("""UPDATE productos SET stock=?, precio_compra=?, precio_venta=?, precio=?, proveedor=? WHERE id=?""",
                                           (nuevo_stock, p_compra, p_venta, p_venta, proveedor, producto_existente["id"]))
                    else:
                        cursor.execute("UPDATE productos SET stock=?, precio_compra=?, proveedor=? WHERE id=?",
                                       (nuevo_stock, p_compra, proveedor, producto_existente["id"]))
                else:
                    precio_venta_final = p_venta if p_venta > 0 else round(p_compra * 1.30, 2)
                    cursor.execute("""INSERT INTO productos
                        (nombre, proveedor, precio_compra, precio_venta, precio, stock, imagen)
                        VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (nombre, proveedor, p_compra, precio_venta_final, precio_venta_final, cant, nombre_prod_img))

            cursor.execute("UPDATE compras_proveedores SET total = ? WHERE id = ?", (total_compra, compra_id))
            conn.commit()

        return jsonify({"status": "success", "message": "Compra guardada correctamente.",
                        "compra_id": compra_id, "total": round(total_compra, 2)})
    except Exception as e:
        app.logger.exception("Error al guardar compra de proveedor")
        return jsonify({"status": "error", "message": "No se pudo guardar la compra. No cierres el sistema y revisa los datos."}), 500


@app.route("/buscar_productos_json")
@login_required
def buscar_productos_json():
  q = request.args.get("q", "").strip()
  if not q:
    return jsonify([])

  with get_db_connection() as conn:
    cursor = conn.cursor()
    cursor.execute("SELECT id, nombre, precio_venta, stock FROM productos WHERE nombre LIKE ? AND stock > 0 LIMIT 10", ("%" + q + "%",))
    filas = cursor.fetchall()

  return jsonify([{"id": f["id"], "nombre": f["nombre"], "precio": f["precio_venta"], "stock": f["stock"]} for f in filas])


@app.route("/buscar_cliente")
@login_required
def buscar_cliente():
  term = request.args.get("term", "").strip()
  if not term:
    return jsonify([])

  resultados = []
  with get_db_connection() as conn:
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT cliente FROM ventas WHERE cliente LIKE ? AND cliente != 'Cliente General' LIMIT 5", ("%" + term + "%",))
    resultados_bd = cursor.fetchall()

  for r in resultados_bd:
    cliente_str = r["cliente"]
    doc_extraido = ""
    nombre_extraido = cliente_str
    if "(" in cliente_str and ")" in cliente_str:
      parts = cliente_str.rsplit(" (", 1)
      nombre_extraido = parts[0]
      doc_extraido = parts[1].replace(")", "").replace("Doc:", "").strip()
    resultados.append({"doc": doc_extraido, "nombre": nombre_extraido, "tipo": "BD"})

  if resultados:
    return jsonify(resultados)

  if term.isdigit():
    if len(term) == 8:
      nombre_dni = consultar_dni_gratis(term)
      if nombre_dni:
        return jsonify([{"doc": term, "nombre": nombre_dni, "tipo": "DNI"}])
    elif len(term) == 11:
      razon_social = consultar_ruc_gratis(term)
      if razon_social:
        return jsonify([{"doc": term, "nombre": razon_social, "tipo": "RUC"}])

  return jsonify(resultados)


@app.route("/imprimir_proforma/<int:id>")
@app.route("/proforma/pdf/<int:id>")
@login_required
def imprimir_proforma(id):
  username = session.get("username", "admin")
  info_tienda = DATOS_SUCURSALES.get(username, DATOS_SUCURSALES["admin"])

  with get_db_connection() as conn:
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM proformas WHERE id = ?", (id,))
    proforma = cursor.fetchone()

    if not proforma:
      return "Proforma no encontrada", 404

    cursor.execute(
        """
            SELECT dp.*, p.nombre 
            FROM detalle_proforma dp
            JOIN productos p ON dp.producto_id = p.id
            WHERE dp.proforma_id = ?
        """,
        (id,),
    )
    detalles = cursor.fetchall()

  filas_html = ""
  for idx, d in enumerate(detalles, start=1):
    filas_html += f"""
        <tr>
            <td style="text-align: center; color: #64748b;">{idx:02d}</td>
            <td style="font-weight: 500; color: #1e293b;">{d['nombre']}</td>
            <td style="text-align: center; color: #334155;">{d['cantidad']}</td>
            <td style="text-align: right; color: #334155;">S/ {d['precio_unitario']:.2f}</td>
            <td style="text-align: right; font-weight: bold; color: #0f172a;">S/ {d['subtotal']:.2f}</td>
        </tr>
        """

  return f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <title>Proforma PROF-{proforma['id']:06d}</title>
        <style>
            * {{ box-sizing: border-box; margin: 0; padding: 0; }}
            body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #ffffff; color: #1e293b; padding: 40px; }}
            .container {{ max-width: 850px; margin: 0 auto; }}
            .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }}
            .company-title {{ color: #e65100; font-size: 28px; font-weight: 800; }}
            .company-info {{ color: #64748b; font-size: 12px; margin-top: 4px; }}
            .badge-box {{ border: 2px solid #e65100; border-radius: 12px; padding: 10px 24px; text-align: center; background-color: #fff8f5; }}
            .badge-title {{ color: #1e293b; font-size: 16px; font-weight: 800; }}
            .badge-number {{ color: #e65100; font-size: 15px; font-weight: bold; }}
            .divider {{ height: 3px; background-color: #e65100; margin-bottom: 25px; border-radius: 2px; }}
            table {{ width: 100%; border-collapse: collapse; margin-bottom: 25px; border: 1px solid #e2e8f0; }}
            th {{ background-color: #334155; color: #ffffff; padding: 12px 14px; text-align: left; font-size: 11px; }}
            td {{ padding: 12px 14px; font-size: 13px; border-bottom: 1px solid #e2e8f0; }}
            .no-print-btn {{ background-color: #e65100; color: white; border: none; padding: 10px 20px; font-weight: bold; border-radius: 6px; cursor: pointer; margin-bottom: 20px; }}
            .print-actions {{ display:flex; gap:8px; margin-bottom:20px; }}
            .print-actions button {{ background:#00BFA5; color:white; border:0; padding:10px 14px; font-weight:bold; cursor:pointer; border-radius:5px; }}
            .print-actions button:last-child {{ background:#334155; }}
            @media print {{ .no-print {{ display:none !important; }} body {{ padding:0; }} }}
            @media print {{ body.print-ticket {{ page: ticket-page; }} body.print-ticket .container {{ width:76mm; max-width:76mm; margin:0; }} body.print-ticket .header {{ display:block; text-align:center; margin-bottom:8px; }} body.print-ticket .company-title {{ font-size:16px; color:#000; }} body.print-ticket .company-info {{ font-size:9px; color:#000; }} body.print-ticket .badge-box {{ border:0; padding:4px 0; background:#fff; }} body.print-ticket .badge-title, body.print-ticket .badge-number {{ font-size:11px; color:#000; }} body.print-ticket .divider {{ height:0; border-top:1px dashed #000; background:none; margin:6px 0; }} body.print-ticket table {{ margin-bottom:8px; border:0; }} body.print-ticket th {{ background:#fff; color:#000; border-bottom:1px dashed #000; padding:4px 2px; font-size:9px; }} body.print-ticket td {{ padding:4px 2px; font-size:9px; overflow-wrap:anywhere; }} }}
            @page ticket-page {{ size:80mm auto; margin:2mm; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="no-print print-actions"><button onclick="document.body.classList.add('print-ticket');window.print();setTimeout(()=>document.body.classList.remove('print-ticket'),500)">🧾 Imprimir ticket 80 mm</button><button onclick="document.body.classList.remove('print-ticket');window.print()">📄 Imprimir A4</button></div>
            <div class="header">
                <div>
                    <img src="/static/logo.jpg" alt="Logo Ferretería Marlon" style="width:55px;height:55px;object-fit:contain;" onerror="this.style.display='none'">
                    <div class="company-title">FERRETERÍA MARLON</div>
                    <div class="company-info">{info_tienda['direccion']} | Tel: {info_tienda['telefono']}</div>
                </div>
                <div class="badge-box">
                    <div class="badge-title">PROFORMA</div>
                    <div class="badge-number">N° PROF-{proforma['id']:06d}</div>
                </div>
            </div>
            <div class="divider"></div>
            <table>
                <thead><tr><th>ITEM</th><th>DESCRIPCIÓN</th><th style="text-align:center;">CANT.</th><th style="text-align:right;">P. UNITARIO</th><th style="text-align:right;">SUBTOTAL</th></tr></thead>
                <tbody>{filas_html}</tbody>
            </table>
        </div>
    </body>
    </html>
    """


@app.route("/imprimir_ticket/<int:id>")
@app.route("/ticket/pdf/<int:id>")
@login_required
def imprimir_ticket(id):
  username = session.get("username", "admin")
  info_tienda = DATOS_SUCURSALES.get(username, DATOS_SUCURSALES["admin"])

  with get_db_connection() as conn:
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM ventas WHERE id = ?", (id,))
    venta = cursor.fetchone()

    if not venta:
      return "Ticket no encontrado", 404

    cursor.execute(
        """
            SELECT dv.*, p.nombre 
            FROM detalle_ventas dv
            JOIN productos p ON dv.producto_id = p.id
            WHERE dv.venta_id = ?
        """,
        (id,),
    )
    detalles = cursor.fetchall()

  filas_html = ""
  for idx, d in enumerate(detalles, start=1):
    filas_html += f"""
        <tr>
            <td style="text-align: center; color: #64748b;">{idx:02d}</td>
            <td style="font-weight: 500; color: #1e293b;">{d['nombre']}</td>
            <td style="text-align: center; color: #334155;">{d['cantidad']}</td>
            <td style="text-align: right; color: #334155;">S/ {d['precio_unitario']:.2f}</td>
            <td style="text-align: right; font-weight: bold; color: #0f172a;">S/ {d['subtotal']:.2f}</td>
        </tr>
        """

  num_comp = venta['comprobante_numero'] if 'comprobante_numero' in venta.keys() and venta['comprobante_numero'] else f"TCK-{venta['id']:06d}"

  return f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <title>Comprobante {num_comp}</title>
        <style>
            * {{ box-sizing: border-box; margin: 0; padding: 0; }}
            body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #ffffff; color: #1e293b; padding: 40px; }}
            .container {{ max-width: 850px; margin: 0 auto; }}
            .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }}
            .company-title {{ color: #0284c7; font-size: 28px; font-weight: 800; }}
            .company-info {{ color: #64748b; font-size: 12px; margin-top: 4px; }}
            .badge-box {{ border: 2px solid #0284c7; border-radius: 12px; padding: 10px 24px; text-align: center; background-color: #f0f9ff; }}
            .badge-title {{ color: #1e293b; font-size: 16px; font-weight: 800; }}
            .badge-number {{ color: #0284c7; font-size: 15px; font-weight: bold; }}
            .divider {{ height: 3px; background-color: #0284c7; margin-bottom: 25px; border-radius: 2px; }}
            table {{ width: 100%; border-collapse: collapse; margin-bottom: 25px; border: 1px solid #e2e8f0; }}
            th {{ background-color: #334155; color: #ffffff; padding: 12px 14px; text-align: left; font-size: 11px; }}
            td {{ padding: 12px 14px; font-size: 13px; border-bottom: 1px solid #e2e8f0; }}
            .no-print-btn {{ background-color: #0284c7; color: white; border: none; padding: 10px 20px; font-weight: bold; border-radius: 6px; cursor: pointer; margin-bottom: 20px; }}
            .print-actions {{ display:flex; gap:8px; margin-bottom:20px; }}
            .print-actions button {{ background:#00BFA5; color:white; border:0; padding:10px 14px; font-weight:bold; cursor:pointer; border-radius:5px; }}
            .print-actions button:last-child {{ background:#334155; }}
            @media print {{ .no-print {{ display:none !important; }} body {{ padding:0; }} }}
            @media print {{ body.print-ticket {{ page: ticket-page; }} body.print-ticket .container {{ width:76mm; max-width:76mm; margin:0; }} body.print-ticket .header {{ display:block; text-align:center; margin-bottom:8px; }} body.print-ticket .company-title {{ font-size:16px; color:#000; }} body.print-ticket .company-info {{ font-size:9px; color:#000; }} body.print-ticket .badge-box {{ border:0; padding:4px 0; background:#fff; }} body.print-ticket .badge-title, body.print-ticket .badge-number {{ font-size:11px; color:#000; }} body.print-ticket .divider {{ height:0; border-top:1px dashed #000; background:none; margin:6px 0; }} body.print-ticket table {{ margin-bottom:8px; border:0; }} body.print-ticket th {{ background:#fff; color:#000; border-bottom:1px dashed #000; padding:4px 2px; font-size:9px; }} body.print-ticket td {{ padding:4px 2px; font-size:9px; overflow-wrap:anywhere; }} }}
            @page ticket-page {{ size:80mm auto; margin:2mm; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="no-print print-actions"><button onclick="document.body.classList.add('print-ticket');window.print();setTimeout(()=>document.body.classList.remove('print-ticket'),500)">🧾 Imprimir ticket 80 mm</button><button onclick="document.body.classList.remove('print-ticket');window.print()">📄 Imprimir A4</button></div>
            <div class="header">
                <div>
                    <img src="/static/logo.jpg" alt="Logo Ferretería Marlon" style="width:55px;height:55px;object-fit:contain;" onerror="this.style.display='none'">
                    <div class="company-title">FERRETERÍA MARLON</div>
                    <div class="company-info">{info_tienda['direccion']} | Tel: {info_tienda['telefono']}</div>
                </div>
                <div class="badge-box">
                    <div class="badge-title">{venta['tipo_comprobante'].upper()}</div>
                    <div class="badge-number">N° {num_comp}</div>
                </div>
            </div>
            <div class="divider"></div>
            <table>
                <thead><tr><th>ITEM</th><th>DESCRIPCIÓN</th><th style="text-align:center;">CANT.</th><th style="text-align:right;">P. UNITARIO</th><th style="text-align:right;">SUBTOTAL</th></tr></thead>
                <tbody>{filas_html}</tbody>
            </table>
        </div>
    </body>
    </html>
    """




@app.route("/gastos", methods=["GET", "POST"])
@login_required
def gestionar_gastos():
    """Lista y registra gastos diarios de la sucursal actualmente iniciada."""
    if request.method == "GET":
        try:
            with get_db_connection() as conn:
                filas = conn.execute(
                    "SELECT id, fecha, descripcion, categoria, metodo_pago, monto FROM gastos ORDER BY fecha DESC, id DESC LIMIT 300"
                ).fetchall()
            return jsonify({"status": "success", "gastos": [dict(fila) for fila in filas]})
        except Exception:
            app.logger.exception("Error al listar gastos")
            return jsonify({"status": "error", "message": "No se pudieron cargar los gastos."}), 500

    datos = request.get_json(silent=True) or request.form
    descripcion = str(datos.get("descripcion", "")).strip()
    categoria = str(datos.get("categoria", "General")).strip() or "General"
    metodo_pago = str(datos.get("metodo_pago", "Efectivo")).strip() or "Efectivo"
    fecha = str(datos.get("fecha", "")).strip()
    try:
        monto = float(datos.get("monto", 0))
        if not descripcion:
            raise ValueError("Escribe el motivo del gasto.")
        if not (monto > 0 and monto < 1_000_000_000):
            raise ValueError("El monto debe ser mayor que cero.")
        if fecha:
            # El input datetime-local envía YYYY-MM-DDTHH:MM.
            fecha = fecha.replace("T", " ")
            datetime.strptime(fecha[:16], "%Y-%m-%d %H:%M")
            fecha = fecha[:16] + ":00"
        else:
            fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if metodo_pago.lower() not in ("efectivo", "yape", "plin", "tarjeta", "transferencia", "otro"):
            raise ValueError("Selecciona un medio de pago válido.")
    except (TypeError, ValueError) as error:
        mensaje = str(error) or "Revisa los datos del gasto."
        return jsonify({"status": "error", "message": mensaje}), 400

    try:
        with get_db_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO gastos (fecha, descripcion, categoria, metodo_pago, monto) VALUES (?, ?, ?, ?, ?)",
                (fecha, descripcion, categoria, metodo_pago, round(monto, 2))
            )
            conn.commit()
            gasto_id = cursor.lastrowid
        return jsonify({"status": "success", "message": "Gasto registrado correctamente.", "id": gasto_id}), 201
    except Exception:
        app.logger.exception("Error al guardar gasto")
        return jsonify({"status": "error", "message": "No se pudo guardar el gasto."}), 500


@app.route("/gastos/<int:gasto_id>", methods=["DELETE"])
@login_required
def eliminar_gasto(gasto_id):
    try:
        with get_db_connection() as conn:
            cursor = conn.execute("DELETE FROM gastos WHERE id = ?", (gasto_id,))
            conn.commit()
            if cursor.rowcount == 0:
                return jsonify({"status": "error", "message": "El gasto ya no existe."}), 404
        return jsonify({"status": "success", "message": "Gasto eliminado."})
    except Exception:
        app.logger.exception("Error al eliminar gasto")
        return jsonify({"status": "error", "message": "No se pudo eliminar el gasto."}), 500


@app.route("/arqueo_caja")
@login_required
def arqueo_caja_por_fechas():
    """Devuelve el arqueo de ventas guardadas en SQLite para un rango de fechas."""
    desde = (request.args.get("desde") or "").strip()
    hasta = (request.args.get("hasta") or "").strip()

    # Validar el formato para evitar rangos ambiguos.
    for etiqueta, valor in (("desde", desde), ("hasta", hasta)):
        if valor:
            try:
                datetime.strptime(valor, "%Y-%m-%d")
            except ValueError:
                return jsonify({
                    "status": "error",
                    "message": f"La fecha '{etiqueta}' debe tener formato AAAA-MM-DD."
                }), 400

    if desde and hasta and desde > hasta:
        return jsonify({
            "status": "error",
            "message": "La fecha inicial no puede ser posterior a la fecha final."
        }), 400

    condiciones = ["UPPER(COALESCE(tipo_comprobante, '')) NOT IN ('PROFORMA', 'COTIZACION', 'COTIZACIÓN')"]
    parametros = []
    if desde:
        condiciones.append("date(fecha) >= date(?)")
        parametros.append(desde)
    if hasta:
        condiciones.append("date(fecha) <= date(?)")
        parametros.append(hasta)

    sql = f"""
        SELECT
            COUNT(*) AS cantidad_ventas,
            COALESCE(SUM(total), 0) AS total_general,
            COALESCE(SUM(CASE WHEN LOWER(TRIM(COALESCE(metodo_pago, ''))) = 'efectivo'
                               OR (LOWER(TRIM(COALESCE(metodo_pago, ''))) NOT IN ('yape', 'plin')
                                   AND LOWER(TRIM(COALESCE(metodo_pago, ''))) NOT LIKE '%tarjeta%'
                                   AND LOWER(TRIM(COALESCE(metodo_pago, ''))) NOT LIKE '%transferencia%')
                              THEN total ELSE 0 END), 0) AS efectivo,
            COALESCE(SUM(CASE WHEN LOWER(TRIM(COALESCE(metodo_pago, ''))) = 'yape' THEN total ELSE 0 END), 0) AS yape,
            COALESCE(SUM(CASE WHEN LOWER(TRIM(COALESCE(metodo_pago, ''))) = 'plin' THEN total ELSE 0 END), 0) AS plin,
            COALESCE(SUM(CASE WHEN LOWER(TRIM(COALESCE(metodo_pago, ''))) LIKE '%tarjeta%' THEN total ELSE 0 END), 0) AS tarjeta,
            COALESCE(SUM(CASE WHEN LOWER(TRIM(COALESCE(metodo_pago, ''))) LIKE '%transferencia%' THEN total ELSE 0 END), 0) AS transferencia
        FROM ventas
        WHERE {" AND ".join(condiciones)}
    """

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, parametros)
            fila = cursor.fetchone()
            resultado = dict(fila) if hasattr(fila, "keys") else dict(zip([col[0] for col in cursor.description], fila))

            condiciones_gastos = ["1=1"]
            parametros_gastos = []
            if desde:
                condiciones_gastos.append("date(fecha) >= date(?)")
                parametros_gastos.append(desde)
            if hasta:
                condiciones_gastos.append("date(fecha) <= date(?)")
                parametros_gastos.append(hasta)
            cursor.execute(f"""
                SELECT COALESCE(SUM(monto), 0) AS total_gastos,
                       COALESCE(SUM(CASE WHEN LOWER(TRIM(metodo_pago)) = 'efectivo' THEN monto ELSE 0 END), 0) AS gastos_efectivo,
                       COALESCE(SUM(CASE WHEN LOWER(TRIM(metodo_pago)) = 'yape' THEN monto ELSE 0 END), 0) AS gastos_yape,
                       COALESCE(SUM(CASE WHEN LOWER(TRIM(metodo_pago)) = 'plin' THEN monto ELSE 0 END), 0) AS gastos_plin,
                       COALESCE(SUM(CASE WHEN LOWER(TRIM(metodo_pago)) LIKE '%tarjeta%' THEN monto ELSE 0 END), 0) AS gastos_tarjeta,
                       COALESCE(SUM(CASE WHEN LOWER(TRIM(metodo_pago)) LIKE '%transferencia%' THEN monto ELSE 0 END), 0) AS gastos_transferencia,
                       COALESCE(SUM(CASE WHEN LOWER(TRIM(metodo_pago)) IN ('otro', 'otros') THEN monto ELSE 0 END), 0) AS gastos_otros
                FROM gastos WHERE {' AND '.join(condiciones_gastos)}
            """, parametros_gastos)
            gastos = dict(cursor.fetchone())

        for clave, valor in resultado.items():
            if clave != "cantidad_ventas":
                resultado[clave] = round(float(valor or 0), 2)
        for clave, valor in gastos.items():
            resultado[clave] = round(float(valor or 0), 2)
        # El arqueo muestra el saldo neto por medio de pago: ventas menos gastos pagados por ese medio.
        resultado["ventas_brutas"] = resultado["total_general"]
        resultado["efectivo"] = round(resultado["efectivo"] - resultado["gastos_efectivo"], 2)
        resultado["yape"] = round(resultado["yape"] - resultado["gastos_yape"], 2)
        resultado["plin"] = round(resultado["plin"] - resultado["gastos_plin"], 2)
        resultado["tarjeta"] = round(resultado["tarjeta"] - resultado["gastos_tarjeta"], 2)
        resultado["transferencia"] = round(resultado.get("transferencia", 0) - resultado.get("gastos_transferencia", 0), 2)
        resultado["total_general"] = round(resultado["ventas_brutas"] - resultado["total_gastos"], 2)
        resultado["cantidad_ventas"] = int(resultado.get("cantidad_ventas") or 0)
        resultado["desde"] = desde or None
        resultado["hasta"] = hasta or None
        return jsonify({"status": "success", "arqueo": resultado})
    except Exception:
        app.logger.exception("Error al consultar el arqueo de caja")
        return jsonify({
            "status": "error",
            "message": "No se pudo consultar el arqueo de caja."
        }), 500


@app.route("/procesar_venta", methods=["POST"])
@app.route("/procesar_venta_pos", methods=["POST"])
@login_required
def procesar_venta_pos():
  data = request.get_json()
  if not data or "items" not in data or len(data["items"]) == 0:
    return jsonify({"status": "error", "message": "Carrito vacío"}), 400

  tipo_comp = (data.get("tipo_comprobante") or data.get("tipo_documento") or "NOTA").upper()
  num_documento = (data.get("cliente_doc") or data.get("num_documento") or data.get("documento") or "").strip()
  cliente_nombre = (data.get("cliente_nombre") or "").strip()
  cliente_recibido = (data.get("cliente") or "").strip()
  
  if cliente_recibido and "(Doc:" in cliente_recibido:
      cliente = cliente_recibido
  elif cliente_nombre and num_documento:
      cliente = f"{cliente_nombre} (Doc: {num_documento})"
  elif cliente_nombre:
      cliente = cliente_nombre
  elif cliente_recibido:
      cliente = cliente_recibido
  else:
      cliente = "Cliente General"

  items = data["items"]
  metodo_pago_recibido = str(data.get("metodo_pago") or data.get("metodoPago") or "Efectivo").strip().lower()
  # Normaliza el medio de pago para que el arqueo siempre lo clasifique correctamente.
  if metodo_pago_recibido in ("yape",):
      metodo_pago = "Yape"
  elif metodo_pago_recibido in ("plin",):
      metodo_pago = "Plin"
  elif "tarjeta" in metodo_pago_recibido or metodo_pago_recibido in ("card", "pos", "visa", "mastercard"):
      metodo_pago = "Tarjeta"
  elif "transferencia" in metodo_pago_recibido or metodo_pago_recibido in ("transfer", "transferencia bancaria"):
      metodo_pago = "Transferencia"
  else:
      # Los registros antiguos sin medio de pago y los valores no reconocidos se
      # muestran en efectivo para que no desaparezcan del desglose de caja.
      metodo_pago = "Efectivo"

  if tipo_comp in ["BOLETA"]:
      serie = "B001"
      correlativo_base = 101
  elif tipo_comp in ["FACTURA"]:
      serie = "F001"
      correlativo_base = 1
  elif tipo_comp in ["PROFORMA", "COTIZACION"]:
      serie = "PROF"
      correlativo_base = 1
  else:
      serie = "NV01"
      correlativo_base = 1000

  with get_db_connection() as conn:
    cursor = conn.cursor()
    try:
      total_venta = sum([
          float(
              i.get("subtotal") or 
              (float(i.get("precio") or i.get("precio_venta") or i.get("precio_unitario") or 0) * 
               int(i.get("cant") or i.get("cantidad") or 1))
          )
          for i in items
      ])
      fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

      if tipo_comp in ["PROFORMA", "COTIZACION"]:
        cursor.execute("INSERT INTO proformas (fecha, cliente, documento, total) VALUES (?, ?, ?, ?)", (fecha_actual, cliente, num_documento, total_venta))
        proforma_id = cursor.lastrowid
        for item in items:
          p_unit = float(item.get("precio") or item.get("precio_venta") or item.get("precio_unitario") or 0)
          cant = int(item.get("cant") or item.get("cantidad") or 1)
          cursor.execute("INSERT INTO detalle_proforma (proforma_id, producto_id, cantidad, precio_unitario, subtotal) VALUES (?, ?, ?, ?, ?)", (proforma_id, item["id"], cant, p_unit, p_unit * cant))
        conn.commit()
        return jsonify({"status": "success", "tipo": "PROFORMA", "proforma_id": proforma_id, "pdf_url": f"/proforma/pdf/{proforma_id}"})
      else:
        cursor.execute("SELECT MAX(correlativo) FROM ventas WHERE serie = ?", (serie,))
        ultimo_corr = cursor.fetchone()[0]
        
        if ultimo_corr is not None:
            nuevo_correlativo = ultimo_corr + 1
        else:
            nuevo_correlativo = correlativo_base

        comprobante_num = f"{serie}-{nuevo_correlativo:06d}"

        cursor.execute(
            """
            INSERT INTO ventas 
            (fecha, cliente, documento, tipo_comprobante, serie, correlativo, comprobante_numero, total, metodo_pago, enviado_sunat) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, 
            (fecha_actual, cliente, num_documento, tipo_comp, serie, nuevo_correlativo, comprobante_num, total_venta, metodo_pago, 1 if tipo_comp in ["BOLETA", "FACTURA"] else 0)
        )
        venta_id = cursor.lastrowid

        for item in items:
          prod_id = item["id"]
          cant = int(item.get("cant") or item.get("cantidad") or 1)
          prec = float(item.get("precio") or item.get("precio_venta") or item.get("precio_unitario") or 0)
          cursor.execute("SELECT precio_compra FROM productos WHERE id = ?", (prod_id,))
          fila_costo = cursor.fetchone()
          costo_unitario = float(fila_costo[0] or 0) if fila_costo else 0.0
          cursor.execute("UPDATE productos SET stock = stock - ? WHERE id = ?", (cant, prod_id))
          cursor.execute("INSERT INTO detalle_ventas (venta_id, producto_id, cantidad, precio_unitario, subtotal, precio_compra_unitario) VALUES (?, ?, ?, ?, ?, ?)", (venta_id, prod_id, cant, prec, prec * cant, costo_unitario))
        
        conn.commit()
        return jsonify({
            "status": "success", 
            "tipo": tipo_comp, 
            "venta_id": venta_id, 
            "comprobante_numero": comprobante_num,
            "pdf_url": f"/imprimir_ticket/{venta_id}"
        })
    except Exception as e:
      conn.rollback()
      return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/obtener_detalle_venta/<int:id_venta>")
@login_required
def obtener_detalle_venta(id_venta):
  try:
    with get_db_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("SELECT cliente, documento, tipo_comprobante, comprobante_numero FROM ventas WHERE id = ?", (id_venta,))
      venta_row = cursor.fetchone()
      
      cliente_str = venta_row["cliente"] if venta_row else ""
      doc_str = venta_row["documento"] if venta_row else ""
      tipo_comp = venta_row["tipo_comprobante"] if venta_row else "NOTA"
      num_comp = venta_row["comprobante_numero"] if venta_row else f"NV01-{id_venta:06d}"

      cursor.execute(
          """
            SELECT 
              COALESCE(p.nombre, 'Producto Ferretería') AS nombre, 
              dv.cantidad AS cant, 
              CASE WHEN dv.precio_unitario > 0 THEN dv.precio_unitario ELSE COALESCE(p.precio_venta, p.precio, 0) END AS precio,
              CASE WHEN dv.subtotal > 0 THEN dv.subtotal ELSE (dv.cantidad * COALESCE(p.precio_venta, p.precio, 0)) END AS subtotal
            FROM detalle_ventas dv
            LEFT JOIN productos p ON dv.producto_id = p.id
            WHERE dv.venta_id = ?
          """,
          (id_venta,),
      )
      filas = cursor.fetchall()
      
      productos = [{"nombre": f["nombre"], "cant": f["cant"], "precio": float(f["precio"]), "subtotal": float(f["subtotal"])} for f in filas]
          
      return jsonify({
          "success": True, 
          "status": "success", 
          "cliente": cliente_str,
          "cliente_doc": doc_str,
          "tipo_comprobante": tipo_comp,
          "comprobante_numero": num_comp,
          "productos": productos
      })
  except Exception as e:
    return jsonify({"success": False, "message": str(e)}), 500


@app.route("/obtener_detalle_compra/<int:id_compra>")
@login_required
def obtener_detalle_compra(id_compra):
  try:
    with get_db_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("SELECT producto_nombre AS nombre, cantidad AS cant, precio_compra, precio_venta FROM detalle_compras WHERE compra_id = ?", (id_compra,))
      filas = cursor.fetchall()
      return jsonify({"success": True, "status": "success", "productos": [dict(f) for f in filas]})
  except Exception as e:
    return jsonify({"success": False, "message": str(e)}), 500


if __name__ == '__main__':
    def iniciar_flask():
        app.run(host='127.0.0.1', port=5000, debug=False)

    # Iniciar Flask en segundo plano (hilo demonio)
    t = threading.Thread(target=iniciar_flask)
    t.daemon = True
    t.start()

    # Abrir ventana nativa independiente con PyWebView
    import time
    time.sleep(0.5)  # Breve pausa para asegurar que Flask inicie
    
    webview.create_window(
        'Ferretería Marlon - Sistema ERP', 
        'http://127.0.0.1:5000', 
        width=1280, 
        height=720,
        resizable=True
    )
