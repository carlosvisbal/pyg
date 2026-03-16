from django.contrib import admin

from .models import (
    Account,
    AccountClassification,
    AccountEditHistory,
    Adjustment,
    AdjustmentDetail,
    AdjustmentHistory,
    Period,
    SAPRecord,
)


# ---------------------------------------------------------------------------
# Inlines
# ---------------------------------------------------------------------------

class AccountInline(admin.TabularInline):
    model = Account
    extra = 0
    fields = ("code", "name", "is_active")
    show_change_link = True


class AdjustmentDetailInline(admin.TabularInline):
    model = AdjustmentDetail
    extra = 1
    autocomplete_fields = ["account"]


class AccountEditHistoryInline(admin.TabularInline):
    model = AccountEditHistory
    extra = 0
    readonly_fields = (
        "action",
        "changes",
        "performed_by",
        "performed_at",
        "note",
    )
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class AdjustmentHistoryInline(admin.TabularInline):
    model = AdjustmentHistory
    extra = 0
    readonly_fields = (
        "action",
        "changes",
        "performed_by",
        "performed_at",
        "note",
    )
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# ModelAdmins
# ---------------------------------------------------------------------------

@admin.register(AccountClassification)
class AccountClassificationAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "parent", "level", "sign", "is_active")
    list_filter = ("is_active", "level")
    search_fields = ("code", "name")
    inlines = [AccountInline]


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "classification", "is_active")
    list_filter = ("is_active", "classification")
    search_fields = ("code", "name")
    autocomplete_fields = ["classification"]
    inlines = [AccountEditHistoryInline]


@admin.register(Period)
class PeriodAdmin(admin.ModelAdmin):
    list_display = ("__str__", "year", "month", "is_closed", "closed_at")
    list_filter = ("is_closed", "year")
    search_fields = ("year",)


@admin.register(SAPRecord)
class SAPRecordAdmin(admin.ModelAdmin):
    list_display = ("account", "period", "amount", "imported_at")
    list_filter = ("period",)
    search_fields = ("account__code", "account__name")
    autocomplete_fields = ["account", "period"]


@admin.register(Adjustment)
class AdjustmentAdmin(admin.ModelAdmin):
    list_display = ("__str__", "period", "created_by", "created_at")
    list_filter = ("period",)
    search_fields = ("description",)
    autocomplete_fields = ["period"]
    inlines = [AdjustmentDetailInline, AdjustmentHistoryInline]


@admin.register(AdjustmentDetail)
class AdjustmentDetailAdmin(admin.ModelAdmin):
    list_display = ("adjustment", "account", "amount")
    autocomplete_fields = ["adjustment", "account"]


@admin.register(AccountEditHistory)
class AccountEditHistoryAdmin(admin.ModelAdmin):
    list_display = ("account", "action", "performed_by", "performed_at")
    list_filter = ("action",)
    search_fields = ("account__code", "account__name")
    readonly_fields = (
        "account",
        "action",
        "changes",
        "performed_by",
        "performed_at",
        "note",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AdjustmentHistory)
class AdjustmentHistoryAdmin(admin.ModelAdmin):
    list_display = ("adjustment", "action", "performed_by", "performed_at")
    list_filter = ("action",)
    readonly_fields = (
        "adjustment",
        "action",
        "changes",
        "performed_by",
        "performed_at",
        "note",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
