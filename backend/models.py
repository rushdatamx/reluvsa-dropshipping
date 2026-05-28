"""Modelos Pydantic para la API del portal dropshipping."""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    token: str
    rol: str
    email: str
    proveedor_id: Optional[int] = None
    proveedor_nombre: Optional[str] = None


class UserInfo(BaseModel):
    user_id: int
    email: str
    rol: str
    proveedor_id: Optional[int] = None


class Proveedor(BaseModel):
    id: int
    nombre: str
    rfc: str
    codigo_bodega: str
    contacto_email: Optional[str] = None
    contacto_nombre: Optional[str] = None
    activo: bool


class ProveedorCreate(BaseModel):
    nombre: str
    rfc: str
    codigo_bodega: str
    contacto_email: Optional[str] = None
    contacto_nombre: Optional[str] = None


class VentaML(BaseModel):
    num_venta: str
    sku: Optional[str]
    fecha_venta: Optional[datetime]
    estado: Optional[str]
    titulo: Optional[str]
    unidades: Optional[int]
    total: Optional[float]
    comprador: Optional[str]
    comprador_estado: Optional[str]
    forma_entrega: Optional[str]


class EnvioColecta(BaseModel):
    num_envio: str
    num_venta: Optional[str]
    fecha_venta: Optional[datetime]
    lugar_indicado: Optional[str]
    lugar_real: Optional[str]
    lugar_override: Optional[str]
    proveedor_id: Optional[int]
    cumplio_sla: Optional[bool]
    excluido_analisis: bool = False


class FacturaConcepto(BaseModel):
    id: int
    codigo_prov: Optional[str]
    descripcion: Optional[str]
    cantidad: Optional[float]
    importe: Optional[float]
    num_venta_match: Optional[str]
    match_method: Optional[str]
    match_confidence: Optional[float]


class Factura(BaseModel):
    id: int
    proveedor_id: int
    uuid_cfdi: Optional[str]
    serie: Optional[str]
    folio: Optional[str]
    rfc_emisor: Optional[str]
    rfc_receptor: Optional[str]
    fecha_factura: Optional[datetime]
    total: Optional[float]
    moneda: Optional[str]
    conceptos: List[FacturaConcepto] = []


class Incidencia(BaseModel):
    id: int
    num_venta: str
    proveedor_id: Optional[int]
    tipo: str
    descripcion: Optional[str]
    estado: str
    created_at: datetime


class IncidenciaCreate(BaseModel):
    num_venta: str
    tipo: str
    descripcion: Optional[str] = None


class ReasignarBodegaRequest(BaseModel):
    lugar_override: str
    proveedor_id: Optional[int] = None


class MetricasProveedor(BaseModel):
    proveedor_id: int
    proveedor_nombre: str
    total_envios: int
    porcentaje_entregas_a_tiempo: float
    tiempo_promedio_facturacion_dias: Optional[float]
    errores_facturacion: int
    dias_desde_ultima_actualizacion_stock: Optional[int]
