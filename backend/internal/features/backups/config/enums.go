package backups_config

type BackupNotificationType string

const (
	NotificationBackupFailed  BackupNotificationType = "BACKUP_FAILED"
	NotificationBackupSuccess BackupNotificationType = "BACKUP_SUCCESS"
)

type BackupEncryption string

const (
	BackupEncryptionNone      BackupEncryption = "NONE"
	BackupEncryptionEncrypted BackupEncryption = "ENCRYPTED"
)

type RetentionPolicyType string

const (
	RetentionPolicyTypeTimePeriod RetentionPolicyType = "TIME_PERIOD"
	RetentionPolicyTypeCount      RetentionPolicyType = "COUNT"
	RetentionPolicyTypeGFS        RetentionPolicyType = "GFS"
)
