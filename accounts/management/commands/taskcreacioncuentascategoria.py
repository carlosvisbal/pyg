from django.core.management.base import BaseCommand
from typing import Optional
import pyodbc


class Command(BaseCommand):
    help = 'Tarea programada que acepta dos parámetros de fecha con el fin de relaizar la operación de cmoprativo de gastos.'

    def add_arguments(self, parser):
        parser.add_argument('sociedad', type=str, help='Sociedad', default='1100', nargs='?')

    def handle(self, *args, **kwargs):
        sociedad = kwargs['sociedad']

        cuentas = self.lista_cuentas(sociedad)
        puc = kwargs['sociedad']

        for cuenta in cuentas:
            nombre_categoria, nombre_subcategoria = self.asignar_categoria_subcategoria(cuenta['CTA. MAYOR'], cuenta['DESCRIPCIÓN'])
            if nombre_categoria == "Código no reconocido" or nombre_subcategoria == "Sin asignación":
                self.stdout.write(self.style.WARNING(f'No se pudo asignar categoría para la cuenta {cuenta["CTA. MAYOR"]}'))
                continue
            # Crear o obtener la categoría
            categoria, creada_categoria = Categoria.objects.get_or_create(
                nombre=nombre_categoria,
                defaults={
                    'puc': puc
                }
            )
            
            # Crear o obtener la subcategoría
            subcategoria, creada_subcategoria = Subcategoria.objects.get_or_create(
                nombre=nombre_subcategoria,
                categoria=categoria,
                defaults={
                    'puc': puc,
                    'codigos_cuenta': cuenta['CTA. MAYOR'][:2]  # Usamos los primeros 2 dígitos como código
                }
            )
            
            # Crear o obtener la cuenta contable
            cuenta_contable, creada_cuenta = CuentaContable.objects.get_or_create(
                nombre=cuenta['CTA. MAYOR'],
                subcategoria=subcategoria,
                defaults={
                    'descripcion': cuenta['DESCRIPCIÓN'],
                    'puc': puc
                }
            )
            
            # Informar sobre las creaciones
            if creada_categoria:
                self.stdout.write(self.style.SUCCESS(f'Categoría creada: {categoria.nombre}'))
            if creada_subcategoria:
                self.stdout.write(self.style.SUCCESS(f'Subcategoría creada: {subcategoria.nombre}'))
            if creada_cuenta:
                self.stdout.write(self.style.SUCCESS(f'Cuenta creada: {cuenta_contable.nombre} - {cuenta_contable.descripcion}'))
            

        return self.stdout.write(self.style.SUCCESS('Cuentas contables creadas correctamente'))

    def puc(self, sociedad):
        sociedad = str(sociedad)
        conn_str = ('DRIVER=ODBC Driver 17 for SQL Server; SERVER=; DATABASE=; UID=;PWD=')
        # Establecer la conexión
        conn = pyodbc.connect(conn_str)
        # Crear un cursor
        cursor = conn.cursor()
        # Query
        query = f"SELECT TOP 1 KTOPL FROM tgp.T001 WHERE BUKRS = '{sociedad}'"
        # Ejecutar la consulta
        cursor.execute(query)
        # Obtener el resultado
        result = cursor.fetchone()
        # Cerrar la conexión
        conn.close()
        # Retornar el resultado
        return result[0] if result else None

    def lista_cuentas(self, sociedad):
        cuentas = self.cuentas_mayores(sociedad)
        return cuentas

    def cuentas_mayores(self, sociedad):
        sociedad = str(sociedad)
        conn_str = ('DRIVER=ODBC Driver 17 for SQL Server; SERVER=; DATABASE=; UID=;PWD=')
        # Establecer la conexión
        conn = pyodbc.connect(conn_str)
        # Crear un cursor
        cursor = conn.cursor()
        # Query
        query = f"SELECT SKA1.KTOPL AS 'PLAN DE CUENTA', SKA1.SAKNR AS 'CTA. MAYOR', TXT50 AS 'DESCRIPCIÓN' FROM tgp.SKA1 INNER JOIN tgp.SKAT ON SKA1.KTOPL = SKAT.KTOPL AND SKA1.SAKNR = SKAT.SAKNR where SKA1.KTOPL = (SELECT TOP 1 KTOPL FROM tgp.T001 where BUKRS='{sociedad}')"
        # Ejecutar la consulta con parámetros (6 parámetros en total)
        cursor.execute(query)
        # Obtener nombres de las columnas
        column_names = [desc[0] for desc in cursor.description]

        # Crear una lista de diccionarios
        cuanta_mayor = [dict(zip(column_names, fetch)) for fetch in cursor]
        return cuanta_mayor if cuanta_mayor else []
        
    def asignar_categoria_subcategoria(self, codigo: str, descripcion: str) -> tuple[str, str]:
        """
        Recibe el código (cadena) y retorna una tupla (categoria, subcategoria) basándose en
        las reglas, validando que el código inicie con el patrón indicado.

        En caso de no cumplirse ningún patrón se retorna ("Código no reconocido", "Sin asignación").
        """
        codigo = codigo.strip()

        # --------------------------- INGRESOS OPERACIONALES ---------------------------
        if codigo.startswith("4120"):
            if codigo.startswith(("4120950100", "4120950300", "4120950600")):
                return ("INGRESOS OPERACIONALES", "Nacionales")
            if codigo.startswith(("4120950200", "4120950700")):
                return ("INGRESOS OPERACIONALES", "Exterior")
            return ("INGRESOS OPERACIONALES", "Otros")

        if codigo.startswith("4130"):
            if codigo.startswith(("4130950200", "4130950400", "4130950500", "4130950600")):
                return ("INGRESOS OPERACIONALES", "Exterior")
            return ("INGRESOS OPERACIONALES", "Otros")

        if codigo.startswith("4132"):
            return ("INGRESOS OPERACIONALES", "POC")

        # --------------------------- INGRESOS OP DEVOLUCIONES Y DESCUENTOS ---------------------------
        if codigo.startswith("417579"):
            return ("INGRESOS OP DEVOLUCIONES Y DESCUENTOS", "Descuentos en ventas")

        # --------------------------- INGRESOS NO OPERACIONALES ---------------------------
        if codigo.startswith("421005"):
            return ("INGRESOS NO OPERACIONALES", "Ingresos no operacionales")

        if codigo.startswith("421020"):
            if codigo.startswith(("4210200100", "4210200300", "4210200500", "4210200600")):
                return ("DIFERENCIA EN CAMBIO REALIZADA", "Diferencia en cambio realizada")
            if codigo.startswith("4210200700"):
                return ("DIFERENCIA EN CAMBIO REALIZADA", "Ingresos operacionales")
            if codigo.startswith(("4210209991", "4210209992", "4210209993", "4210209995", "4210209996", "4210209997")):
                return ("DIFERENCIA EN CAMBIO NO REALIZADA", "Diferencia en cambio no realizada")
            return ("DIFERENCIA EN CAMBIO REALIZADA", "Otros")

        if codigo.startswith(("421040", "421095")):
            return ("INGRESOS NO OPERACIONALES", "Ingresos no operacionales")

        # --------------------------- GASTOS DE ADMINISTRACION ---------------------------
        if codigo.startswith("5105"):
            if codigo.startswith(("5105030100", "5105060100", "5105150100")):
                return ("GASTOS DE ADMINISTRACION", "Gastos de personal")
            return ("GASTOS DE ADMINISTRACION", "Otros")

        if codigo.startswith(("5110100100", "5115300100")):
            return ("GASTOS DE ADMINISTRACION", "Honorarios")
        if codigo.startswith(("5120150100", "5125050100")):
            return ("GASTOS DE ADMINISTRACION", "Arrendamientos")
        if codigo.startswith(("5130050100", "5135050100")):
            return ("GASTOS DE ADMINISTRACION", "Contribuciones y afiliaciones")
        if codigo.startswith("5140050100"):
            return ("GASTOS DE ADMINISTRACION", "Seguros")

        # --------------------------- GASTOS DE VENTAS ---------------------------
        if codigo.startswith("5205"):
            if codigo.startswith("5205050100"):
                return ("GASTOS DE VENTAS", "Gastos de personal")
            if codigo.startswith(("5210100100", "5215300100")):
                return ("GASTOS DE VENTAS", "Honorarios")
            if codigo.startswith(("5220150100", "5225050100")):
                return ("GASTOS DE VENTAS", "Arrendamientos")
            if codigo.startswith(("5230050100", "5235050100")):
                return ("GASTOS DE VENTAS", "Contribuciones y afiliaciones")
            if codigo.startswith("5240050100"):
                return ("GASTOS DE VENTAS", "Seguros")
            return ("GASTOS DE VENTAS", "Otros")

        # --------------------------- GASTOS FINANCIEROS ---------------------------
        if codigo.startswith("5305"):
            if codigo.startswith(("5305030100", "5305060100")):
                return ("GASTOS FINANCIEROS", "Intereses y gastos financieros")
            if codigo.startswith("5310100100"):
                return ("GASTOS FINANCIEROS", "Comisiones")
            return ("GASTOS FINANCIEROS", "Otros")

        # --------------------------- INGRESOS GENERALES ---------------------------
        if codigo.startswith("4000"):
            if codigo.startswith("4005030100"):
                return ("INGRESOS", "Ventas de productos")
            if codigo.startswith("4010100100"):
                return ("INGRESOS", "Intereses y otros ingresos")
            return ("INGRESOS", "Otros")

        # --------------------------- GASTOS OPERATIVOS ---------------------------
        if codigo.startswith("5100"):
            if codigo.startswith("5105030100"):
                return ("GASTOS OPERATIVOS", "Gastos de personal")
            if codigo.startswith("5110100100"):
                return ("GASTOS OPERATIVOS", "Honorarios")
            return ("GASTOS OPERATIVOS", "Otros")

        # --------------------------- MANO DE OBRA ---------------------------
        if codigo.startswith("7205"):
            # todos los subprefijos de mano de obra directa
            directos = ("7205060100","7205150100","7205150300","7205150400", 
                        "7205150500","7205150600","7205150700","7205150800", 
                        "7205240100","7205270100","7205300100","7205330100", 
                        "7205360100","7205390100","7205600100","7205680100", 
                        "7205690100","7205700100","7205720100","7205750100", 
                        "7205780100","7205950300")
            if any(codigo.startswith(p) for p in directos):
                return ("MANO DE OBRA", "Mano de obra directa")
            if codigo.startswith("7205150200"):
                return ("MANO DE OBRA", "Mano de obra")
            return ("MANO DE OBRA", "Otros")

        # --------------------------- COSTOS INDIRECTOS DE FABRICACIÓN ---------------------------
        if codigo.startswith("7301"):
            if codigo.startswith(("7301010100", "7301010101", "7301011100")):
                return ("COSTOS INDIRECTOS DE FABRICACIÓN", "Materiales indirectos")
        if codigo.startswith("7305"):
            # mano de obra indirecta e instalaciones
            indirectos = ("7305060100","7305150100","7305150200","7305150300", 
                        "7305150400","7305150500","7305150600","7305150700", 
                        "7305150800","7305240100","7305270100")
            if any(codigo.startswith(p) for p in indirectos):
                return ("MANO DE OBRA", "Mano de obra indirecta e instalaciones")
        if codigo.startswith("7310") and codigo.startswith(("7310250200", "7310950100")):
            return ("COSTOS INDIRECTOS DE FABRICACIÓN", "Honorarios")
        if codigo.startswith("7315") and codigo.startswith("7315400100"):
            return ("COSTOS INDIRECTOS DE FABRICACIÓN", "Impuestos")
        if codigo.startswith("7320") and codigo.startswith(("7320100100", "7320150100")):
            return ("COSTOS INDIRECTOS DE FABRICACIÓN", "Arrendamientos")
        if codigo.startswith("7325") and codigo.startswith("7325050100"):
            return ("COSTOS INDIRECTOS DE FABRICACIÓN", "Contribuciones y afiliaciones")
        if codigo.startswith("7330") and codigo.startswith(("7330100100", "7330200100")):
            return ("COSTOS INDIRECTOS DE FABRICACIÓN", "Seguros")
        if codigo.startswith("7335") and codigo.startswith("7335050100"):
            return ("COSTOS INDIRECTOS DE FABRICACIÓN", "Servicios")
        if codigo.startswith("7340") and codigo.startswith("7340050100"):
            return ("COSTOS INDIRECTOS DE FABRICACIÓN", "Legales")
        if codigo.startswith("7345") and codigo.startswith(("7345100100", "7345150100")):
            return ("COSTOS INDIRECTOS DE FABRICACIÓN", "Mantenimientos")
        if codigo.startswith("7350") and codigo.startswith("7350150100"):
            return ("COSTOS INDIRECTOS DE FABRICACIÓN", "Adecuaciones")
        if codigo.startswith("7355") and codigo.startswith("7355050100"):
            return ("COSTOS INDIRECTOS DE FABRICACIÓN", "Gastos de viajes")
        if codigo.startswith("7360") and codigo.startswith(("7360050100", "7360100100")):
            return ("COSTOS INDIRECTOS DE FABRICACIÓN", "Depreciación")
        if codigo.startswith("7365") and codigo.startswith(("7365050200", "7365100200")):
            return ("COSTOS INDIRECTOS DE FABRICACIÓN", "Amortizaciones")
        if codigo.startswith("7395") and codigo.startswith(("7395200100", "7395250100")):
            return ("COSTOS INDIRECTOS DE FABRICACIÓN", "Diversos")
        if codigo.startswith("7399") and codigo.startswith("7399999999"):
            return ("COSTOS INDIRECTOS DE FABRICACIÓN", "Provisiones")

        # Cualquier otro caso que no encaje en las categorías anteriores
        return ("Código no reconocido", "Sin asignación")
