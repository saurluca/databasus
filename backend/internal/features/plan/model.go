package plans

import (
	"databasus-backend/internal/util/period"

	"github.com/google/uuid"
)

type DatabasePlan struct {
	DatabaseID uuid.UUID `json:"databaseId" gorm:"column:database_id;type:uuid;primaryKey;not null"`

	MaxBackupSizeMB       int64             `json:"maxBackupSizeMb"       gorm:"column:max_backup_size_mb;type:int;not null"`
	MaxBackupsTotalSizeMB int64             `json:"maxBackupsTotalSizeMb" gorm:"column:max_backups_total_size_mb;type:int;not null"`
	MaxStoragePeriod      period.TimePeriod `json:"maxStoragePeriod"      gorm:"column:max_storage_period;type:text;not null"`
}

func (p *DatabasePlan) TableName() string {
	return "database_plans"
}
