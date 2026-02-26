package storages

import (
	"context"
	"databasus-backend/internal/util/encryption"
	"io"
	"log/slog"

	"github.com/google/uuid"
)

type StorageFileSaver interface {
	SaveFile(
		ctx context.Context,
		encryptor encryption.FieldEncryptor,
		logger *slog.Logger,
		fileName string,
		file io.Reader,
	) error

	GetFile(encryptor encryption.FieldEncryptor, fileName string) (io.ReadCloser, error)

	DeleteFile(encryptor encryption.FieldEncryptor, fileName string) error

	Validate(encryptor encryption.FieldEncryptor) error

	TestConnection(encryptor encryption.FieldEncryptor) error

	HideSensitiveData()

	EncryptSensitiveData(encryptor encryption.FieldEncryptor) error
}

type StorageDatabaseCounter interface {
	GetStorageAttachedDatabasesIDs(storageID uuid.UUID) ([]uuid.UUID, error)
}
