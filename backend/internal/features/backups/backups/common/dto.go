package common

import (
	backups_config "databasus-backend/internal/features/backups/config"
	"errors"

	"github.com/google/uuid"
)

type BackupMetadata struct {
	BackupID       uuid.UUID                       `json:"backupId"`
	EncryptionSalt *string                         `json:"encryptionSalt"`
	EncryptionIV   *string                         `json:"encryptionIV"`
	Encryption     backups_config.BackupEncryption `json:"encryption"`
}

func (m *BackupMetadata) Validate() error {
	if m.BackupID == uuid.Nil {
		return errors.New("backup ID is required")
	}

	if m.Encryption == "" {
		return errors.New("encryption is required")
	}

	if m.Encryption == backups_config.BackupEncryptionEncrypted {
		if m.EncryptionSalt == nil {
			return errors.New("encryption salt is required when encryption is enabled")
		}

		if m.EncryptionIV == nil {
			return errors.New("encryption IV is required when encryption is enabled")
		}
	}

	return nil
}
