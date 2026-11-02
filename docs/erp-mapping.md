# Mapeo de campos: modelo interno ↔ Dynamics 365 F&O ↔ SAP S/4HANA

✅ confirmado en fuente oficial · 🟡 simplificado en el mock (ver notas)

## Pedido de venta — cabecera

| Campo interno | Dynamics 365 F&O | SAP S/4HANA |
|---|---|---|
| Entidad | ✅ `SalesOrderHeadersV2` | ✅ `API_SALES_ORDER_SRV` → `A_SalesOrder` |
| Número de pedido | ✅ `SalesOrderNumber` | ✅ `SalesOrder` |
| Cliente | ✅ `OrderingCustomerAccountNumber` (+ `InvoiceCustomerAccountNumber`) | ✅ `SoldToParty` |
| Referencia externa (ID de propuesta) | 🟡 `CustomersOrderReference` | ✅ `PurchaseOrderByCustomer` |
| Moneda | ✅ `CurrencyCode` | ✅ `TransactionCurrency` |
| Fecha solicitada | ✅ `RequestedShippingDate` | ✅ `RequestedDeliveryDate` |
| Total neto | (suma de líneas) | ✅ `TotalNetAmount` |
| Datos organizativos | — | ✅ `SalesOrderType`, `SalesOrganization`, `DistributionChannel`, `OrganizationDivision` |

## Pedido de venta — líneas / posiciones

| Campo interno | Dynamics 365 F&O | SAP S/4HANA |
|---|---|---|
| Entidad | ✅ `SalesOrderLines` | ✅ `A_SalesOrderItem` (navegación `to_Item`) |
| Producto | ✅ `ItemNumber` | ✅ `Material` |
| Cantidad | ✅ `OrderedSalesQuantity` | ✅ `RequestedQuantity` |
| Unidad | ✅ `SalesUnitSymbol` | ✅ `RequestedQuantityUnit` |
| Precio unitario | ✅ `SalesPrice` | 🟡 condiciones de precio |
| Descuento % | 🟡 `LineDiscountPercentage` | 🟡 condición de precio |
| Monto de línea | ✅ `LineAmount` | ✅ `NetAmount` |
| Bodega | 🟡 `ShippingWarehouseId` | ✅ `ProductionPlant` |

## Lecturas

| Dato | Dynamics 365 F&O | SAP S/4HANA |
|---|---|---|
| Producto y precio | ✅ `ReleasedProductsV2`: `ItemNumber`, `SalesPrice`, `SalesUnitSymbol` | 🟡 `API_PRODUCT_SRV` → `A_Product` |
| Stock por bodega | ✅ `InventWarehouseOnHandV2Entity`: `ItemNumber`, `InventoryWarehouseId`, `AvailableOnHandQuantity` | ✅ `API_MATERIAL_STOCK_SRV` → `A_MatlStkInAcctMod`: `Material`, `Plant`, `MatlWrhsStkQtyInMatlBaseUnit` |
| Cliente y crédito | ✅ `CustomersV3`: `CustomerAccount`, `OrganizationName`, `CreditLimit` | 🟡 módulo de gestión de crédito |
| Estado del producto | 🟡 | 🟡 |

## Notas sobre las simplificaciones

1. **Precio/descuento en SAP:** en SAP el precio resulta de condiciones de precio. El mock entrega el precio de lista ya resuelto; en producción el adaptador leería las condiciones y el resto del sistema no cambia.
2. **Crédito en SAP:** vive en el módulo de gestión de crédito; en el mock es un endpoint simple con límite y monto usado.
3. **Nivel de cliente y descuento máximo:** no son campos estándar; en la práctica son grupos de cliente o acuerdos comerciales.
4. **Estado del producto:** Dynamics usa estados de ciclo de vida y SAP marcas por planta; el mock usa `active` / `discontinued`.
5. **Campos 🟡 de Dynamics:** siguen la convención de nombres, pero la página oficial solo lista un subconjunto de campos.
6. **Convención:** Microsoft documenta los nombres en mayúsculas (formato de importación); vía OData se usan en PascalCase.

## Fuentes

- [Sales order headers V2](https://learn.microsoft.com/en-us/dynamics365/fin-ops-core/dev-itpro/data-entities/entity-sales-order-headers-v2-salesorderheaderv2)
- [Sales order lines V2](https://learn.microsoft.com/en-us/dynamics365/fin-ops-core/dev-itpro/data-entities/entity-sales-order-lines-v2-salesorderline)
- [Released products V2](https://learn.microsoft.com/en-sg/dynamics365/fin-ops-core/dev-itpro/data-entities/entity-released-products-v2-releasedproductv2)
- [Inventory on-hand by warehouse V2](https://learn.microsoft.com/en-us/common-data-model/schema/core/operationscommon/entities/supplychain/inventoryandwarehousemanagement/inventwarehouseonhandv2entity)
- [Customers V3](https://learn.microsoft.com/da-dk/dynamics365/fin-ops-core/dev-itpro/data-entities/entity-customers-v3-customerv3)
- SAP Cloud SDK (generado de la definición oficial): [SalesOrder](https://unpkg.com/@sap/cloud-sdk-vdm-sales-order-service@1.28.2/SalesOrder.d.ts), [SalesOrderItem](https://unpkg.com/@sap/cloud-sdk-vdm-sales-order-service@1.28.2/SalesOrderItem.d.ts)
- [SAP Material Stock](https://help.sap.com/doc/98598d91753e472da1235b2aa36babc5/1.0/en-US/classes/_sap_cloud_sdk_vdm_material_stock_service.matlstkinacctmod.html)
