from app.core.database import Base
from app.models.enterprise_quota import (
    EnterpriseCostResource,
    EnterpriseQuotaComponent,
    EnterpriseQuotaItem,
    EnterpriseQuotaSection,
    EnterpriseQuotaVersion,
    CostImportBatch,
    QUOTA_VERSION_STATUS_DRAFT,
    RESOURCE_TYPE_AUXILIARY_MATERIAL,
)


def test_enterprise_quota_tables_are_registered_in_metadata():
    expected_tables = {
        "cost_import_batches",
        "enterprise_quota_versions",
        "enterprise_quota_sections",
        "enterprise_quota_items",
        "enterprise_quota_components",
        "enterprise_cost_resources",
    }

    assert expected_tables.issubset(set(Base.metadata.tables))


def test_enterprise_quota_model_relationships_preserve_master_data_shape():
    batch = CostImportBatch(
        batch_uuid="batch-001",
        source_filename="广东旗胜-企业定额1.0（20260626）.xls",
        source_file_sha256="a" * 64,
        parser_version="phase0",
    )
    version = EnterpriseQuotaVersion(
        version_code="QS-20260626-v1",
        version_name="广东旗胜企业定额1.0",
        status=QUOTA_VERSION_STATUS_DRAFT,
    )
    section = EnterpriseQuotaSection(section_code="QS201", section_name="块料楼地面工程", sort_order=1)
    item = EnterpriseQuotaItem(
        quota_code="QS201001",
        item_name="石材地面（正铺）",
        unit="m2",
        unit_price=71.13,
        labor_fee=60,
        auxiliary_material_fee=11.13,
        sort_order=2,
    )
    resource = EnterpriseCostResource(
        resource_code="09CA0240",
        resource_name="砂子",
        resource_type=RESOURCE_TYPE_AUXILIARY_MATERIAL,
        unit="m3",
        price=85,
    )
    component = EnterpriseQuotaComponent(
        parent_quota_code="QS201001",
        component_type="CB辅材",
        resource_code="09CA0240",
        resource_name="砂子",
        unit="m3",
        quantity=0.04,
        unit_price=85,
        amount=3.4,
        fee_bucket=RESOURCE_TYPE_AUXILIARY_MATERIAL,
    )

    batch.versions.append(version)
    version.sections.append(section)
    version.items.append(item)
    version.resources.append(resource)
    version.components.append(component)
    item.components.append(component)
    resource.components.append(component)

    assert version.import_batch is batch
    assert item.version is version
    assert item.section is None
    item.section = section
    assert section.items == [item]
    assert component.version is version
    assert component.quota_item is item
    assert component.resource is resource


def test_enterprise_quota_table_columns_cover_phase1_requirements():
    item_columns = Base.metadata.tables["enterprise_quota_items"].columns
    component_columns = Base.metadata.tables["enterprise_quota_components"].columns
    resource_columns = Base.metadata.tables["enterprise_cost_resources"].columns

    for column_name in (
        "quota_code",
        "item_name",
        "work_content",
        "unit",
        "unit_price",
        "labor_fee",
        "main_material_fee",
        "auxiliary_material_fee",
        "machinery_fee",
        "source_sheet",
        "source_row_index",
        "raw_row_json",
    ):
        assert column_name in item_columns

    for column_name in ("parent_quota_code", "component_type", "resource_code", "resource_name", "quantity", "unit_price", "amount", "fee_bucket"):
        assert column_name in component_columns

    for column_name in ("resource_code", "resource_name", "resource_type", "unit", "price", "tax_rate", "price_block_label"):
        assert column_name in resource_columns
